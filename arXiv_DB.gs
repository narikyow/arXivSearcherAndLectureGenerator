// ==========================================
// グローバル設定項目
// ==========================================
// 【重要】ウェブアプリとしてデプロイした後に発行されるURLをここに貼り付けてください
const WEB_APP_URL = 'DUMMY_APP'; 

// PDFを保存するGoogle DriveのフォルダIDを指定してください
const DRIVE_FOLDER_ID = 'DUMMY_ID'; 

function fetchAndTranslateArxivPapers() {
  const SCRIPT_START_TIME = Date.now();
  const MAX_EXECUTION_TIME_MS = 4.5 * 60 * 1000; // 4分30秒 (GASの6分制限に対する安全マージン)

  // ==========================================
  // 検索・通知設定項目
  // ==========================================
  // 【修正点】arXiv APIの仕様に合わせ、各語句・フレーズに「all:」プレフィックスを付与。
  // ダブルクォーテーションを内部で使用するため、全体をシングルクォートで囲みます。
  const keyword = '((all:evolutionary OR all:evolution) AND (all:graph OR all:graphs OR all:game OR all:games)) OR all:"evolutionary computation" OR all:"differential evolution" OR all:"evolutionary algorithms" OR all:"complex system" OR all:"complex systems" OR all:"complex network" OR all:"complex networks"';
  
  const emailAddress = 'DUMMY';
  const targetOutputCount = 5;
  const fetchCount = 20;
  
  const spreadsheetId = 'DUMMY';
  const sheetName = 'シート1';

  const properties = PropertiesService.getScriptProperties();

  // ==========================================
  // 1. スプレッドシートから履歴と最古の日時を読み込む
  // ==========================================
  const sheet = SpreadsheetApp.openById(spreadsheetId).getSheetByName(sheetName);
  if (!sheet) {
    Logger.log('指定されたシートが見つかりません。');
    return;
  }
  
  const data = sheet.getDataRange().getValues();
  const readTitles = [];
  
  let oldestTime = Date.now();
  let hasHistory = false;

  for (let i = 0; i < data.length; i++) {
    if (data[i][0]) {
      readTitles.push(data[i][0].toString().trim());
    }
    if (data[i][2]) {
      const pDate = new Date(data[i][2]).getTime();
      if (!isNaN(pDate) && pDate < oldestTime) {
        oldestTime = pDate;
        hasHistory = true;
      }
    }
  }
  
  let newPapers = [];
  let isFirstSearch = true;
  
  let currentSearchEndTime = oldestTime; 
  const SEARCH_WINDOW_MONTHS = 12;

  // ==========================================
  // 2. arXiv APIの呼び出し（目標件数または制限時間に達するまでループ）
  // ==========================================
  while (newPapers.length < targetOutputCount) {
    if (Date.now() - SCRIPT_START_TIME > MAX_EXECUTION_TIME_MS) {
      Logger.log('GASの実行時間制限が近づいたため、API探索ループを打ち切ります。');
      break;
    }

    // 【修正点】複雑な論理式をそのまま渡すため、全体の「all:""」による囲い込みを廃止。
    let searchQueryStr = keyword;
    
    if (!isFirstSearch) {
      if (!hasHistory) {
        Logger.log('過去の日時履歴がないため、遡り検索を終了します。');
        break;
      }
      
      const endDateObj = new Date(currentSearchEndTime);
      endDateObj.setMinutes(endDateObj.getMinutes() - 1);
      
      const startDateObj = new Date(endDateObj.getTime());
      startDateObj.setMonth(startDateObj.getMonth() - SEARCH_WINDOW_MONTHS);
      
      const toDateStr = formatArxivDate(endDateObj);
      const fromDateStr = formatArxivDate(startDateObj);
      
      // 日付検索とのAND条件を結合。元のクエリ全体を確実に評価させるため括弧で保護します。
      searchQueryStr = `(${searchQueryStr}) AND submittedDate:[${fromDateStr} TO ${toDateStr}]`;
      Logger.log(`過去論文を検索します。期間: ${fromDateStr} 〜 ${toDateStr}`);
      
      currentSearchEndTime = startDateObj.getTime();
    } else {
      Logger.log('最新の論文を検索します。');
    }

    const lastRequestTimeStr = properties.getProperty('LAST_ARXIV_API_REQUEST_TIME');
    const currentTime = new Date().getTime();
    if (lastRequestTimeStr) {
      const timeElapsed = currentTime - parseInt(lastRequestTimeStr, 10);
      if (timeElapsed < 3000) {
        Utilities.sleep(3000 - timeElapsed);
      }
    }

    const searchQuery = encodeURIComponent(searchQueryStr);
    const url = `http://export.arxiv.org/api/query?search_query=${searchQuery}&sortBy=submittedDate&sortOrder=descending&start=0&max_results=${fetchCount}`;
    
    let response;
    let isSuccess = false;
    const maxRetries = 3;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        properties.setProperty('LAST_ARXIV_API_REQUEST_TIME', new Date().getTime().toString());
        response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
        const responseCode = response.getResponseCode();
        
        if (responseCode === 200) {
          isSuccess = true;
          break;
        } else if (responseCode === 429 || responseCode === 503) {
          const waitSeconds = 15 * Math.pow(2, attempt); 
          Logger.log(`APIエラー (${responseCode})。${waitSeconds}秒待機し再試行します...`);
          Utilities.sleep(waitSeconds * 1000); 
        } else {
          Logger.log(`予期せぬHTTPエラーコード: ${responseCode}`);
          break;
        }
      } catch (e) {
        Logger.log(`UrlFetchAppエラー: ${e.message}`);
        if (attempt === maxRetries) break;
        Utilities.sleep(15 * Math.pow(2, attempt) * 1000);
      }
    }
    
    if (!isSuccess) {
      Logger.log('APIからのデータ取得に失敗したため、探索を中断します。');
      break;
    }
    
    const xml = response.getContentText();
    const document = XmlService.parse(xml);
    const root = document.getRootElement();
    const atom = XmlService.getNamespace('http://www.w3.org/2005/Atom');
    const entries = root.getChildren('entry', atom);
    
    if (entries.length === 0 && !isFirstSearch) {
      Logger.log('指定期間内に該当する論文が見つかりませんでした。次の期間へ進みます。');
    }

    for (let i = 0; i < entries.length; i++) {
      if (newPapers.length >= targetOutputCount) break;
      
      const entry = entries[i];
      const title = entry.getChild('title', atom).getText().replace(/\n/g, ' ').trim();
      
      if (readTitles.indexOf(title) === -1) {
        const id = entry.getChild('id', atom).getText();
        const summary = entry.getChild('summary', atom).getText().replace(/\n/g, ' ').trim();
        const published = entry.getChild('published', atom).getText();
        
        const authorElements = entry.getChildren('author', atom);
        let authorsArray = [];
        for (let j = 0; j < authorElements.length; j++) {
          authorsArray.push(authorElements[j].getChild('name', atom).getText());
        }
        
        newPapers.push({
          id: id,
          title: title,
          authors: authorsArray.join(', '),
          summary: summary,
          published: published,
          link: id,
          translatedSummary: ''
        });
        
        readTitles.push(title);
      }
    }
    
    isFirstSearch = false;
  }
  
  if (newPapers.length === 0) {
    Logger.log('新規に通知する論文はありませんでした。');
    return;
  }
  
  // ==========================================
  // 3. 翻訳処理とHTMLメール文面の生成
  // ==========================================
  let htmlBody = `
    <div style="font-family: 'Helvetica Neue', Arial, sans-serif; max-width: 800px; margin: 0 auto; color: #333;">
      <h2 style="color: #b31b1b; border-bottom: 2px solid #b31b1b; padding-bottom: 8px;">
        arXiv論文通知
      </h2>
      <p><strong>取得件数:</strong> ${newPapers.length}件</p>
  `;
  
  for (let i = 0; i < newPapers.length; i++) {
    const paper = newPapers[i];
    let translatedTitle = '';
    let translatedSummaryHTML = '';
    let plainTranslatedSummary = '';
    
    if (Date.now() - SCRIPT_START_TIME > MAX_EXECUTION_TIME_MS) {
      Logger.log('GASの実行時間制限が近づいたため、残りの翻訳をスキップします。');
      translatedTitle = `${paper.title} <span style="color:red; font-size: 0.9em;">(未翻訳)</span>`;
      translatedSummaryHTML = paper.summary;
      plainTranslatedSummary = paper.summary;
    } else {
      try {
        translatedTitle = LanguageApp.translate(paper.title, 'en', 'ja');
        plainTranslatedSummary = LanguageApp.translate(paper.summary, 'en', 'ja');
        translatedSummaryHTML = plainTranslatedSummary;
        Utilities.sleep(1000); 
      } catch (e) {
        translatedTitle = paper.title;
        translatedSummaryHTML = `<span style="color:red;">[翻訳エラー: ${e.message}]</span><br><br>${paper.summary}`;
        plainTranslatedSummary = `[翻訳エラー: ${e.message}]\n\n${paper.summary}`;
      }
    }
    
    paper.translatedSummary = plainTranslatedSummary;

    const formatHtmlText = (text) => {
      if (!text) return '';
      let escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      escaped = escaped.replace(/\$([^$]+)\$/g, '<code style="font-family: Consolas, Monaco, monospace; background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; color: #d63384;">$$$1$$</code>');
      return escaped.replace(/\n/g, '<br>');
    };

    const pdfUrl = paper.link.replace('/abs/', '/pdf/') + '.pdf';
    
    // URLの連結部分の & を &amp; に変更し、HTML構文として正当な形式に修正
    const downloadLink = `${WEB_APP_URL}?url=${encodeURIComponent(pdfUrl)}&amp;title=${encodeURIComponent(paper.title)}`;

    htmlBody += `
      <div style="margin-bottom: 30px; border: 1px solid #ddd; border-radius: 6px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
        <h3 style="margin-top: 0; color: #0056b3;">【${i + 1}】 ${formatHtmlText(translatedTitle)}</h3>
        <p style="margin: 4px 0; font-size: 0.95em;">
          <strong>原題:</strong> ${formatHtmlText(paper.title)}<br>
          <strong>著者:</strong> ${formatHtmlText(paper.authors)}<br>
          <strong>投稿:</strong> ${paper.published}<br>
          <strong>URL:</strong> <a href="${paper.link}" style="color: #0056b3; text-decoration: none;">${paper.link}</a>
        </p>
        
        <div style="margin-top: 10px;">
          <a href="${downloadLink}" target="_blank" style="display: inline-block; padding: 8px 16px; background-color: #28a745; color: white; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 0.9em;">📥 Google DriveにPDFを保存</a>
        </div>

        <div style="background-color: #f8f9fa; border-left: 4px solid #ced4da; padding: 10px 15px; margin-top: 15px; font-size: 0.95em; line-height: 1.6;">
          <strong>[アブストラクト]</strong><br>
          ${formatHtmlText(translatedSummaryHTML)}
        </div>
      </div>
    `;
  }
  
  htmlBody += `</div>`;
  
  // ==========================================
  // 4. メール送信およびスプレッドシートへの記録処理
  // ==========================================
  const subject = `【arXiv自動取得】指定条件の論文 ${newPapers.length}件`;
  
  try {
    MailApp.sendEmail({
      to: emailAddress,
      subject: subject,
      body: 'お使いのメールクライアントはHTMLメールをサポートしていません。',
      htmlBody: htmlBody
    });
    Logger.log(`${newPapers.length}件の論文をHTMLメールで送信しました。`);
    
    for (let i = 0; i < newPapers.length; i++) {
      sheet.appendRow([newPapers[i].title, newPapers[i].authors, newPapers[i].published, newPapers[i].translatedSummary]);
    }
    Logger.log('スプレッドシートに論文情報を記録しました。');
    
  } catch (e) {
    Logger.log('メール送信またはスプレッドシート記録中にエラーが発生しました: ' + e.message);
  }
}

function formatArxivDate(dateObj) {
  const pad = (n) => n.toString().padStart(2, '0');
  const yyyy = dateObj.getUTCFullYear();
  const MM = pad(dateObj.getUTCMonth() + 1);
  const dd = pad(dateObj.getUTCDate());
  const hh = pad(dateObj.getUTCHours());
  const mm = pad(dateObj.getUTCMinutes());
  return `${yyyy}${MM}${dd}${hh}${mm}`;
}

// ==========================================
// 5. ウェブアプリのエンドポイント (GETリクエスト受信処理)
// ==========================================
function doGet(e) {
  try {
    const pdfUrl = e.parameter.url;
    let title = e.parameter.title || e.parameter['amp;title'] || 'arXiv_Paper';
    
    title = title.replace(/[\/\\]/g, '_');

    if (!pdfUrl) {
      return HtmlService.createHtmlOutput('<h3 style="color:red;">エラー: PDFのURLが指定されていません。</h3>');
    }

    const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    const fileName = title + '.pdf';
    const existingFiles = folder.getFilesByName(fileName);

    // すでに同じ名前のファイルが存在するかチェック
    if (existingFiles.hasNext()) {
      const existingFile = existingFiles.next();
      const html = `
        <div style="font-family: sans-serif; max-width: 500px; margin: 40px auto; text-align: center; border: 1px solid #ddd; padding: 30px; border-radius: 8px;">
          <h2 style="color: #17a2b8;">保存スキップ</h2>
          <p>以下の論文PDFはすでにGoogle Driveに保存されています。</p>
          <p style="font-weight: bold; background: #f4f4f4; padding: 10px; border-radius: 4px; word-break: break-all;">${title}</p>
          <div style="margin-top: 20px;">
            <a href="${existingFile.getUrl()}" target="_blank" style="display: inline-block; padding: 10px 20px; background-color: #0056b3; color: white; text-decoration: none; border-radius: 4px;">Driveでファイルを確認する</a>
          </div>
        </div>
      `;
      return HtmlService.createHtmlOutput(html);
    }

    const response = UrlFetchApp.fetch(pdfUrl, { 
      muteHttpExceptions: true,
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      }
    });
    
    if (response.getResponseCode() !== 200) {
       return HtmlService.createHtmlOutput(`<h3 style="color:red;">エラー: PDFの取得に失敗しました (HTTP ${response.getResponseCode()})。</h3>`);
    }

    const blob = response.getBlob().setName(fileName);
    const file = folder.createFile(blob);

    const html = `
      <div style="font-family: sans-serif; max-width: 500px; margin: 40px auto; text-align: center; border: 1px solid #ddd; padding: 30px; border-radius: 8px;">
        <h2 style="color: #28a745;">保存完了</h2>
        <p>以下の論文PDFをGoogle Driveに保存しました。</p>
        <p style="font-weight: bold; background: #f4f4f4; padding: 10px; border-radius: 4px; word-break: break-all;">${title}</p>
        <div style="margin-top: 20px;">
          <a href="${file.getUrl()}" target="_blank" style="display: inline-block; padding: 10px 20px; background-color: #0056b3; color: white; text-decoration: none; border-radius: 4px;">Driveでファイルを確認する</a>
        </div>
      </div>
    `;
    return HtmlService.createHtmlOutput(html);

  } catch (error) {
    return HtmlService.createHtmlOutput(`<h3 style="color:red;">処理中にエラーが発生しました: ${error.message}</h3>`);
  }
}