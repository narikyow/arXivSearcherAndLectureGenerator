import os
import io
import time
import json
import re
import logging
import urllib.request
import urllib.error
import smtplib
from email.mime.text import MIMEText
import google.auth
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai
from google.genai import types  
from notion_client import Client  

# ==========================================
# 0. ログの抑制（404エラーなどの内部警告をミュート）
# ==========================================
logging.getLogger("google").setLevel(logging.CRITICAL)
logging.getLogger("google.api_core").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)

# ==========================================
# 1. 設定
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
PARENT_PAGE_ID = os.environ.get("PARENT_PAGE_ID")

DB_FOLDER_ID = "DUMMY"
LECTURE_FOLDER_ID = "DUMMY"

SYSTEM_PROMPT = """
あなたは、対象分野の優れた専門書や学術書の著者のような、高度な専門知識と卓越した教育的視点を持つ専門家です。
これから提示する文献・論文について、提案手法そのものをいきなり解説するのではなく、「ユーザーがこの論文を自力で完全に理解するために必要な前提知識・基礎理論」を抽出し、ユーザーの基礎知識レベルを起点として段階的に講義してください。
論理の厳密性と正確性を最優先としつつ、直観的・具体的な説明を併記することで分かりやすさを担保してください。迎合的な態度は不要です。客観的かつ専門的な視点から、率直に、かつ非常に詳細に解説を行います。文章は「です・ます」調で統一してください。

# 最重要目標
本講義の最大の目的は、「ユーザーの専門外領域の知識（純粋数学や未知の物理・情報科学分野など）を補完し、論文を読むための確固たる土台を構築すること」です。論文の提案手法自体の詳細な証明よりも、「前提知識の堅牢な構築」にリソースを集中させてください。

# ユーザーの前提知識
* 専門分野：[差分進化アルゴリズム]
* 基礎知識レベル：[高校数学・線形代数の基礎、高校程度、大学工学部初等程度の微積の基礎は理解できる。数学科の知識は皆無であり、体や環、フベニの定理、ルベーグ積分などといった純粋数学領域の知見はない。また、物理学についても高校の基礎的な力学、電磁気学が理解できる程度であり、統計物理学や量子力学などといった分野についての理解は無い]

# 講義の進行ルールと履歴管理（API実行要件）
* 抽出した前提知識の量に応じてカリキュラムを構築しますが、APIのコストと長大化を防ぐため、**最大でも全15回程度**で完結するようにスケジュールを調整してください。
* 第1回（初回の出力）で、全何回の講義になるか、および抽出された前提知識のリスト（カリキュラム全体像）を明示してください。
* 【超重要】毎回の出力の最後に、次回への引き継ぎ用として「今回の講義で解説した内容の要約（200文字以内）」を必ず作成し、<SUMMARY>要約内容</SUMMARY> のタグで囲んで出力してください。
* 未知の領域（純粋数学など）であっても、数式展開や理論の説明における省略は極力行わないでください。
* 数式をただしくマークダウンで表示できない場合は評価が下がります。最終出力前にマークダウンがただしく機能しているか検証してください。

# 禁止事項（コスト・ノイズ削減）
* **コード生成の禁止**: トークン消費を抑え、ユーザーの理論理解に直結しないノイズを排除するため、**C++やPython、疑似コードなどのプログラムコードの出力は一切行わないでください**。すべてのリソースを数式と理論の解説に割いてください。

# 解説の品質基準（遵守事項）
優れた専門書の教育的アプローチに倣い、以下の基準を厳守してください。
1. **直前での定義の徹底**：新しい数式記号、変数、関数、専門用語を使用する際は、必ず使用する直前にその定義と意味を明確に記述すること。
2. **導出の非省略とステップバイステップの展開**：「自明である」「〜と展開できる」といった飛躍を避け、各変形の根拠や理論の推移を明記すること。
3. **具体から抽象への段階的アプローチ**：最初から高度に抽象的な定義を与えるのではなく、まずは分野における具体的なモデルや既知の概念を用いて視覚化・説明し、その後に一般化・抽象化を行うこと。
4. **多角的な視点の提供**：数式的な操作の詳細と、それが持つ本質的・直観的な意味合いの両方を行き来しながら示すこと。
5. **論理と直観の併記**：厳密な論理的・理論的説明を行った後、それが物理的、幾何学的に何を意味するのか、直観的な説明を必ず併記すること。

# カリキュラム（出力構成の指針）
## 【第1回：論文の概要と必要知識の抽出】
* 論文の書誌情報と、何についての論文かの簡潔な客観的概要。
* **[重要] 知識の抽出**： この論文の理論、定式化、背景を理解するために必要な既存の理論、手法、数学的公式、専門用語を漏れなく抽出すること。
* 抽出した要素を「ユーザーの基礎知識からの接続」「一般的な専門レベルの理論」「論文固有の高度な前提手法」の階層に分け、今後の講義スケジュール（最大15回以内）を提示すること。

## 【第2回〜第N-2回：前提理論と公式の基礎講義】
* ユーザーの専門外の手法や理論について、ユーザーの基礎知識を起点として、具体的なモデルや既知の概念（橋渡し）を用いながら詳細に解説すること。
* 論文内で当たり前のように使われている基礎公式や定理があれば、その導出と証明を厳密に行うこと。

## 【第N-1回：前提知識と論文の接続】
* これまで解説した基礎知識が、論文の問題提起やベースライン手法においてどのように適用されているかを解説すること。
* 論文の数式モデル（定式化）を読むための準備として、論文固有の記号定義と、基礎理論との対応関係を整理すること。

## 【第N回（最終回）：提案手法の直観的理解と要点】
* 論文の提案手法について、厳密な証明は深く追わず、これまで構築した前提知識をベースに「どのようなアイデアで既存の課題を解決したのか」を直観的かつ論理的に要約すること。
* 提案手法の目的、全体的な処理フロー、および今後の課題について1500文字程度で客観的に総括すること。
"""

# ==========================================
# 2. 関数定義と時間差アクセスの設定
# ==========================================
def print_retry_log(retry_state):
    exception = retry_state.outcome.exception()
    print(f"  [待機中] 一時的なエラーのため時間をおいて再試行します (試行回数: {retry_state.attempt_number}). エラー詳細: {exception}", flush=True)

# 修正箇所: Flex推論の遅延を考慮し、最大試行回数と最大待機時間を大幅に延長
@retry(stop=stop_after_attempt(60), wait=wait_exponential(multiplier=2, min=5, max=600), before_sleep=print_retry_log)
def generate_content_with_retry(client, contents, system_prompt=None, cached_content_name=None):
    # 修正箇所: Flex推論を指定
    config_kwargs = {
        "service_tier": 'flex'
    }
    
    if cached_content_name:
        config_kwargs["cached_content"] = cached_content_name
    elif system_prompt:
        config_kwargs["system_instruction"] = system_prompt
        
    return client.models.generate_content(
        model="gemini-3.7-flash",
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs)
    )

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def count_tokens_with_retry(client, contents):
    return client.models.count_tokens(
        model="gemini-3.7-flash",
        contents=contents
    ).total_tokens

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def list_drive_files_with_retry(service, **kwargs):
    return service.files().list(**kwargs).execute()

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def download_file_with_retry(service, file_id, save_path):
    request = service.files().get_media(fileId=file_id)
    with open(save_path, "wb") as fh:
        fh.write(request.execute())

def delete_drive_file(service, file_id):
    """Drive上のファイルを削除します。エラーは基本的に権限関係のためリトライしません。"""
    service.files().delete(fileId=file_id).execute()
        
@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def upload_file_to_gemini_with_retry(client, file_path):
    return client.files.upload(file=file_path)

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def upload_markdown_with_retry(folder_id, file_name, content):
    GAS_URL = "DUMMY"
    payload = {
        "folderId": folder_id,
        "filename": file_name,
        "content": content
    }
    req = urllib.request.Request(
        GAS_URL, 
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    urllib.request.urlopen(req)

def get_drive_service():
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials, project = google.auth.default(scopes=scopes)
    return build('drive', 'v3', credentials=credentials)

def send_alert_email(file_name, error_msg):
    """削除失敗時にアラートメールを送信します"""
    sender_email = os.environ.get("ALERT_EMAIL_ADDRESS")
    sender_password = os.environ.get("ALERT_EMAIL_PASSWORD")

    if not sender_email or not sender_password:
        print("  -> [メール送信スキップ] ALERT_EMAIL_ADDRESS または ALERT_EMAIL_PASSWORD が未設定です。", flush=True)
        return

    body = f"不要な講義ファイル（不完全なデータ等）の削除に失敗しました。\nファイルの所有権エラーの可能性があります。\n\n対象ファイル: {file_name}\nエラー詳細: {error_msg}\n\nGoogle Driveから手動で削除してください。"
    msg = MIMEText(body)
    msg['Subject'] = f"[Bot通知] Driveファイルの自動削除失敗: {file_name}"
    msg['From'] = sender_email
    msg['To'] = sender_email

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print("  -> [通知] 管理者へ警告メールを送信しました。", flush=True)
    except Exception as e:
        print(f"  -> [通知エラー] メールの送信に失敗しました: {e}", flush=True)


# --- Notionアップロード用関数群 ---
@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def get_notion_child_pages_with_retry(notion, parent_id):
    pages = set()
    has_more = True
    start_cursor = None
    while has_more:
        response = notion.blocks.children.list(
            block_id=parent_id,
            start_cursor=start_cursor
        )
        for block in response.get("results", []):
            if block.get("type") == "child_page":
                title = block.get("child_page", {}).get("title")
                if title:
                    pages.add(title)
        has_more = response.get("has_more", False)
        start_cursor = response.get("next_cursor")
    return pages

def parse_rich_text(text):
    rich_text_list = []
    pattern = re.compile(r'(\$\$.*?\$\$|\$.*?\$|\*\*.*?\*\*|`.*?`)')
    parts = pattern.split(text)
    
    for part in parts:
        if not part:
            continue
            
        if part.startswith('$$') and part.endswith('$$') and len(part) >= 4:
            expr = part[2:-2].strip()
            if expr:
                rich_text_list.append({"type": "equation", "equation": {"expression": expr}})
            else:
                rich_text_list.append({"type": "text", "text": {"content": part}})
        elif part.startswith('$') and part.endswith('$') and len(part) >= 2 and not part.startswith('$$'):
            expr = part[1:-1].strip()
            if expr:
                rich_text_list.append({"type": "equation", "equation": {"expression": expr}})
            else:
                rich_text_list.append({"type": "text", "text": {"content": part}})
        elif part.startswith('**') and part.endswith('**') and len(part) >= 4:
            content = part[2:-2]
            rich_text_list.append({
                "type": "text",
                "text": {"content": content[:2000]},
                "annotations": {"bold": True}
            })
        elif part.startswith('`') and part.endswith('`') and len(part) >= 2:
            content = part[1:-1]
            rich_text_list.append({
                "type": "text",
                "text": {"content": content[:2000]},
                "annotations": {"code": True}
            })
        else:
            for i in range(0, len(part), 2000):
                rich_text_list.append({
                    "type": "text",
                    "text": {"content": part[i:i+2000]}
                })
                
    return rich_text_list

def parse_markdown_text_to_blocks(md_text):
    blocks = []
    lines = md_text.split('\n')
    
    in_math_block = False
    in_code_block = False
    block_content = []
    code_language = "plain text"
    
    for raw_line in lines:
        line = raw_line.strip()
        
        if line.startswith("```"):
            if in_code_block:
                content = "\n".join(block_content)
                blocks.append({
                    "object": "block", "type": "code",
                    "code": {
                        "rich_text": [{"type": "text", "text": {"content": content[:2000]}}],
                        "language": code_language
                    }
                })
                in_code_block = False
                block_content = []
            else:
                in_code_block = True
                lang = line[3:].strip().lower()
                lang_map = {
                    "python": "python", "javascript": "javascript", "js": "javascript",
                    "typescript": "typescript", "ts": "typescript", "html": "html", 
                    "css": "css", "c": "c", "c++": "c++", "cpp": "c++", "java": "java", 
                    "bash": "bash", "sh": "bash", "json": "json", "markdown": "markdown", 
                    "sql": "sql"
                }
                code_language = lang_map.get(lang, "plain text")
            continue
            
        if in_code_block:
            block_content.append(raw_line)
            continue

        if line == "$$":
            if in_math_block:
                blocks.append({
                    "object": "block", "type": "equation",
                    "equation": {"expression": "\n".join(block_content)}
                })
                in_math_block = False
                block_content = []
            else:
                in_math_block = True
            continue
            
        if in_math_block:
            block_content.append(raw_line.strip('\n'))
            continue

        if line.startswith("$$") and line.endswith("$$") and len(line) >= 4:
            expr = line[2:-2].strip()
            if expr:
                blocks.append({
                    "object": "block", "type": "equation",
                    "equation": {"expression": expr}
                })
            continue

        if not line:
            continue

        if line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": parse_rich_text(line[4:])}})
        elif line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": parse_rich_text(line[3:])}})
        elif line.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1", "heading_1": {"rich_text": parse_rich_text(line[2:])}})
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parse_rich_text(line[2:])}})
        elif re.match(r'^\d+\.\s', line):
            content = re.sub(r'^\d+\.\s+', '', line)
            blocks.append({"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": parse_rich_text(content)}})
        elif line.startswith("> "):
            blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": parse_rich_text(line[2:])}})
        elif line == "---" or line == "***":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": parse_rich_text(line)}})
            
    return blocks

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), before_sleep=print_retry_log)
def upload_to_notion_with_retry(title, content):
    if not NOTION_TOKEN or not PARENT_PAGE_ID:
        print("  -> [Notion送信スキップ] NOTION_TOKEN または PARENT_PAGE_ID が未設定です。", flush=True)
        return
        
    notion = Client(auth=NOTION_TOKEN)
    blocks = parse_markdown_text_to_blocks(content)
    
    print(f"  -> Notionページを作成中... ({title})", flush=True)
    new_page = notion.pages.create(
        parent={"type": "page_id", "page_id": PARENT_PAGE_ID},
        properties={
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        }
    )
    page_id = new_page["id"]
    
    print(f"  -> Notionへ {len(blocks)} 個のブロックを転送中...", flush=True)
    chunk_size = 100
    for i in range(0, len(blocks), chunk_size):
        chunk = blocks[i:i+chunk_size]
        notion.blocks.children.append(block_id=page_id, children=chunk)
        
    print(f"  -> Notion送信完了: ページID {page_id}", flush=True)


# ==========================================
# 3. メイン処理
# ==========================================
def main():
    if not API_KEY:
        print("エラー: GEMINI_API_KEY が設定されていません。", flush=True)
        return

    client = genai.Client(api_key=API_KEY)
    service = get_drive_service()
    
    # ---------------------------------------------------------
    # Notion側のページ一覧取得
    # ---------------------------------------------------------
    print("【状態確認】Notion側のページ一覧を取得中...", flush=True)
    notion_client = Client(auth=NOTION_TOKEN) if (NOTION_TOKEN and PARENT_PAGE_ID) else None
    existing_notion_pages = set()
    
    if notion_client:
        try:
            existing_notion_pages = get_notion_child_pages_with_retry(notion_client, PARENT_PAGE_ID)
            print(f"  -> Notion側に {len(existing_notion_pages)} 件のページを確認しました。", flush=True)
        except Exception as e:
            print(f"  -> Notionのページ一覧取得に失敗しました: {e}", flush=True)

        # Notionに存在しないDrive側のMDファイルは不要と見なして一括削除
        print("【整理処理】Notionに存在しないDrive上の講義ファイル(.md)を確認し削除します...", flush=True)
        page_token = None
        drive_md_files = []
        while True:
            results = list_drive_files_with_retry(
                service,
                q=f"'{LECTURE_FOLDER_ID}' in parents and name contains '.md' and trashed=false",
                fields="files(id, name), nextPageToken",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            )
            drive_md_files.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break

        for md_file in drive_md_files:
            md_name = md_file['name']
            notion_title = md_name.replace(".md", "")
            
            if notion_title not in existing_notion_pages:
                print(f"  -> [削除対象] Notionに存在しないファイルを発見: {md_name}", flush=True)
                try:
                    delete_drive_file(service, md_file['id'])
                    print(f"  -> [削除完了] {md_name} をDriveから削除しました。", flush=True)
                except Exception as e:
                    print(f"  -> [削除失敗] 権限がないため削除できませんでした。メールで通知します。", flush=True)
                    send_alert_email(md_name, str(e))
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # PDFファイルの処理ループ（Geminiを用いた講義生成）
    # ---------------------------------------------------------
    pdf_files = []
    page_token = None
    while True:
        results = list_drive_files_with_retry(
            service,
            q=f"'{DB_FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false",
            orderBy="createdTime",
            fields="files(id, name), nextPageToken",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        )
        pdf_files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    print(f"【確認】Driveから検知されたPDFの数: {len(pdf_files)}件", flush=True)

    for pdf in pdf_files:
        pdf_name = pdf['name']
        lecture_name = pdf_name.replace(".pdf", "_Lecture.md")
        notion_title = lecture_name.replace(".md", "")

        # スキップ判定をNotion側のページ存在有無に変更
        if notion_title in existing_notion_pages:
            print(f"スキップ: {notion_title} は既にNotionに存在します。", flush=True)
            continue
        
        print(f"処理開始: {pdf_name}", flush=True)
        local_pdf_path = f"temp_{pdf['id']}.pdf"
        uploaded_pdf = None
        cached_name = None
        lecture_content = ""
        is_completed = False 
        
        try:
            download_file_with_retry(service, pdf['id'], local_pdf_path)
            uploaded_pdf = upload_file_to_gemini_with_retry(client, local_pdf_path)
            
            print("  -> PDFファイルを解析中... (API側の準備完了を待機)", flush=True)
            while True:
                state_str = str(uploaded_pdf.state).upper()
                if "PROCESSING" in state_str:
                    time.sleep(3)
                    uploaded_pdf = client.files.get(name=uploaded_pdf.name)
                else:
                    break
                    
            if "FAILED" in str(uploaded_pdf.state).upper():
                print(f"  -> 警告: {pdf_name} の解析に失敗しました。スキップします。", flush=True)
                continue
                
            # --- コンテキストキャッシュの構築 ---
            try:
                print("  -> トークン数を計測中...", flush=True)
                token_count = count_tokens_with_retry(client, [uploaded_pdf, SYSTEM_PROMPT])
                print(f"  -> PDF + プロンプトのトークン数: {token_count}", flush=True)
                
                if token_count >= 32768:
                    print("  -> トークン数が基準を満たすため、コンテキストキャッシュを作成します...", flush=True)
                    cache = client.caches.create(
                        model="gemini-3.7-flash",
                        config=types.CreateCachedContentConfig(
                            contents=[uploaded_pdf],
                            system_instruction=SYSTEM_PROMPT,
                            ttl="3600s"
                        )
                    )
                    cached_name = cache.name
                    print(f"  -> キャッシュ作成完了 ({cached_name})。コストを大幅に削減します。", flush=True)
            except Exception as e:
                print(f"  -> キャッシュ作成をスキップ（フォールバック）: {e}", flush=True)
            # -----------------------------------------------
            
            print("  -> 第1回のリクエストを送信中...", flush=True)
            if cached_name:
                contents_first = ["添付された論文に基づき、第1回の講義（カリキュラム構築）を出力してください。"]
            else:
                contents_first = [
                    uploaded_pdf,
                    "添付された論文に基づき、第1回の講義（カリキュラム構築）を出力してください。"
                ]
            
            response = generate_content_with_retry(
                client=client, 
                contents=contents_first, 
                system_prompt=SYSTEM_PROMPT if not cached_name else None, 
                cached_content_name=cached_name
            )
            
            try:
                first_response_text = response.text
            except (IndexError, AttributeError, TypeError, ValueError):
                print(f"  -> 警告: {pdf_name} の出力がブロックされました。スキップします。", flush=True)
                continue
                
            lecture_content = first_response_text + "\n\n---\n\n"
            if "[COMPLETED_ALL_LECTURES]" in first_response_text:
                is_completed = True
            
            accumulated_summaries = ""
            match = re.search(r'<SUMMARY>(.*?)</SUMMARY>', first_response_text, re.DOTALL)
            if match:
                accumulated_summaries += f"【第1回の要約】: {match.group(1).strip()}\n"
                
            previous_response_text = first_response_text
            
            # --- メインループ ---
            for i in range(2, 31):
                if is_completed:
                    break
                    
                print(f"  -> 第{i}回のリクエストを送信中...", flush=True)
                
                next_prompt = f"""
【これまでの講義の要約（長期記憶）】
{accumulated_summaries}

【直前回の講義の出力（短期記憶）】
{previous_response_text}

【指示】
あなたは第1回でこの論文のカリキュラムを構築しました。
上記の「これまでの講義の要約」と「直前回の講義の出力」を踏まえ、カリキュラムに沿って論理が破綻しないように前回の続きとなる「第{i}回」の講義を出力してください。
出力の末尾には、次回へ引き継ぐために「今回の講義内容の要約（200文字以内）」を必ず <SUMMARY>要約内容</SUMMARY> の形式で付与してください。

【終了判定ルール（最重要）】
今回の「第{i}回」の解説をもってカリキュラムがすべて完了する（最終回である）場合に限り、<SUMMARY>タグの外側（一番最後）に「[COMPLETED_ALL_LECTURES]」という文字列を1回だけ出力してください。
まだ講義が続く場合は、絶対にこの文字列を出力しないでください。
"""
                
                if cached_name:
                    contents_next = ["【第1回の出力（カリキュラム全体像）】\n" + first_response_text + "\n\n" + next_prompt]
                else:
                    contents_next = [
                        uploaded_pdf, 
                        "【第1回の出力（カリキュラム全体像）】\n" + first_response_text + "\n\n" + next_prompt
                    ]
                
                response = generate_content_with_retry(
                    client=client, 
                    contents=contents_next, 
                    system_prompt=SYSTEM_PROMPT if not cached_name else None, 
                    cached_content_name=cached_name
                )
                
                try:
                    current_text = response.text
                except (IndexError, AttributeError, TypeError, ValueError):
                    print(f"  -> 警告: 第{i}回の出力がブロックされました。ここで処理を打ち切ります。", flush=True)
                    break
                    
                lecture_content += current_text + "\n\n---\n\n"
                
                if "[COMPLETED_ALL_LECTURES]" in current_text:
                    is_completed = True
                    print("  -> 講義の完結を検知しました。", flush=True)
                    break
                
                match = re.search(r'<SUMMARY>(.*?)</SUMMARY>', current_text, re.DOTALL)
                if match:
                    accumulated_summaries += f"【第{i}回の要約】: {match.group(1).strip()}\n"
                
                previous_response_text = current_text
                time.sleep(2)

            # --- 未完結時のリカバリ機構 ---
            if not is_completed:
                print("  -> 規定回数内で講義が完結しませんでした。完結に向けたリカバリリクエストを送信します...", flush=True)
                for j in range(1, 6):
                    print(f"  -> リカバリ 第{j}回のリクエストを送信中...", flush=True)
                    recovery_prompt = f"""
【これまでの講義の要約（長期記憶）】
{accumulated_summaries}

【直前回の講義の出力（短期記憶）】
{previous_response_text}

【指示】
カリキュラムがまだ完結していません。論理が破綻しないように前回の続きから講義を再開し、残りのカリキュラムをすべて解説してください。
今回の解説で講義がすべて完了する場合に限り、必ず出力の一番最後に「[COMPLETED_ALL_LECTURES]」という文字列を1回だけ出力してください。
まだ講義が続く場合は、次回のために要約タグ <SUMMARY>要約内容</SUMMARY> を出力してください。
"""
                    if cached_name:
                        contents_recovery = ["【第1回の出力（カリキュラム全体像）】\n" + first_response_text + "\n\n" + recovery_prompt]
                    else:
                        contents_recovery = [
                            uploaded_pdf, 
                            "【第1回の出力（カリキュラム全体像）】\n" + first_response_text + "\n\n" + recovery_prompt
                        ]

                    response = generate_content_with_retry(
                        client=client, 
                        contents=contents_recovery, 
                        system_prompt=SYSTEM_PROMPT if not cached_name else None, 
                        cached_content_name=cached_name
                    )
                    
                    try:
                        current_text = response.text
                    except (IndexError, AttributeError, TypeError, ValueError):
                        print(f"  -> 警告: リカバリ出力がブロックされました。", flush=True)
                        break

                    lecture_content += current_text + "\n\n---\n\n"
                    
                    if "[COMPLETED_ALL_LECTURES]" in current_text:
                        is_completed = True
                        print("  -> リカバリリクエストにより講義の完結を検知しました。", flush=True)
                        break
                        
                    match = re.search(r'<SUMMARY>(.*?)</SUMMARY>', current_text, re.DOTALL)
                    if match:
                        accumulated_summaries += f"【リカバリ第{j}回の要約】: {match.group(1).strip()}\n"
                    
                    previous_response_text = current_text
                    time.sleep(2)

        except Exception as e:
            print(f"  -> エラー発生 ({pdf_name}): 処理中に例外が発生しました（{e}）。中途半端なデータは破棄します。", flush=True)
            is_completed = False 
            
        finally:
            # --- 完結時のみDriveとNotionへ保存（中断時は破棄） ---
            if is_completed and lecture_content:
                clean_content = re.sub(r'<SUMMARY>.*?</SUMMARY>', '', lecture_content, flags=re.DOTALL)
                clean_content = re.sub(r'\n{3,}', '\n\n', clean_content)
                
                # 1. Driveへのアップロード
                try:
                    upload_markdown_with_retry(LECTURE_FOLDER_ID, lecture_name, clean_content)
                    print(f"完了: {lecture_name} をDriveにアップロードしました。", flush=True)
                except Exception as e:
                    print(f"Driveアップロードエラー: {e}", flush=True)
                
                # 2. Notionへのアップロード
                try:
                    upload_to_notion_with_retry(notion_title, clean_content)
                except Exception as e:
                    print(f"Notionアップロードエラー: {e}", flush=True)
            else:
                print(f"  -> 警告: {pdf_name} の処理は途中で中断されたか、完結しなかったため、生成されたデータを破棄しました（Drive・Notionへは送信されません）。", flush=True)
                
            # 【クリーンアップ】
            if os.path.exists(local_pdf_path):
                os.remove(local_pdf_path)
                
            if cached_name:
                try:
                    client.caches.delete(name=cached_name)
                    print("  -> コンテキストキャッシュを削除しました。", flush=True)
                except Exception:
                    pass
            if uploaded_pdf:
                try:
                    client.files.delete(name=uploaded_pdf.name)
                except Exception:
                    pass

if __name__ == '__main__':
    main()