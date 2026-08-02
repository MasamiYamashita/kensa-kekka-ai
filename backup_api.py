# -*- coding: utf-8 -*-
from fastapi import APIRouter
from backup import backup_to_drive
from save import DB_PATH

router = APIRouter()


@router.post("/backup")
def run_backup():
    # 失敗時はここで例外を投げっぱなしにする(Dify側のノードが赤く失敗表示になるようにするため)
    backup_to_drive(DB_PATH)
    return {"status": "ok"}
