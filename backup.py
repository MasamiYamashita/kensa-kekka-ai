# -*- coding: utf-8 -*-
"""lab_resultsテーブルをCSV化し、Google Sheetsとしてバックアップするモジュール(runtime用、ブラウザ操作なし)"""
import csv
import io
import os
import sqlite3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

DRIVE_FILENAME = "lab_results_backup"  # 拡張子なし(Googleスプレッドシートとして保存される)


def get_credentials():
    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError(
            "token.jsonがありません。先に setup_drive_auth.py を実行して認証してください。"
        )
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def export_csv(db_path: str) -> bytes:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT exam_date, exam_time, dialysis_type, name, result, result_text, unit, reference "
        "FROM lab_results ORDER BY exam_date, exam_time, name"
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    # スプレッドシートは人が読むので、見出しは日本語のままにする
    writer.writerow(["検査日", "時刻", "透析区分", "項目名", "検査結果", "検査結果_原文", "単位", "基準値"])
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def backup_to_drive(db_path: str):
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    # 同名ファイルが既にあれば上書き、無ければ新規作成(バックアップを1ファイルに保つ)
    results = service.files().list(
        q=f"name='{DRIVE_FILENAME}' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    files = results.get("files", [])

    csv_bytes = export_csv(db_path)
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv")

    if files:
        service.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        metadata = {
            "name": DRIVE_FILENAME,
            "mimeType": "application/vnd.google-apps.spreadsheet",  # CSVをスプレッドシートに変換して保存
        }
        service.files().create(body=metadata, media_body=media).execute()
