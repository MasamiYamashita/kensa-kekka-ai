# -*- coding: utf-8 -*-
from fastapi import APIRouter
from backup import DRIVE_FILENAME_N8N, backup_to_drive
from save import DB_PATH
from trend_graph import N8N_DB_PATH

router = APIRouter()


@router.post("/backup")
def run_backup():
    # 失敗時はここで例外を投げっぱなしにする(Dify側のノードが赤く失敗表示になるようにするため)
    backup_to_drive(DB_PATH)
    return {"status": "ok"}


@router.post("/backup_n8n")
def run_backup_n8n():
    # n8n版(架空データ)専用。実データのバックアップとは別ファイルに書き込む
    backup_to_drive(N8N_DB_PATH, DRIVE_FILENAME_N8N)
    return {"status": "ok"}
