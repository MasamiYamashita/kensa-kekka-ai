# -*- coding: utf-8 -*-
"""
Google Driveバックアップの初回認証用スクリプト。
一度だけ実行するとブラウザが開き、ログイン許可後に token.json が保存される。
以降の自動バックアップはこの token.json を使って(ブラウザ操作なしで)動作する。
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    print(f"認証完了。{TOKEN_PATH} を保存しました。")


if __name__ == "__main__":
    main()
