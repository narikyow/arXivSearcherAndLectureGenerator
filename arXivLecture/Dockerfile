# ベースイメージとして公式の軽量なPythonイメージを使用
FROM python:3.11-slim

ENV PYTHONBUFFERED=1

# コンテナ内の作業ディレクトリを設定
WORKDIR /app

# 依存関係のリストをコンテナ内にコピー
COPY requirements.txt .

# パッケージのインストール（キャッシュを残さない設定でイメージサイズを削減）
RUN pip install --no-cache-dir -r requirements.txt

# 実行するスクリプト本体をコンテナ内にコピー
COPY script.py .

# コンテナ起動時に実行されるコマンド
CMD ["python", "-u","script.py"]