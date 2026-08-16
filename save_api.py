# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from save import save_items

router = APIRouter()


class SaveRequest(BaseModel):
    date: str          # 採取日 "26/7/20"
    time: str          # 採取時刻 "21:00"
    dialysis: str = ""  # 帳票の原文をそのまま受け取る。save_items内で正規化される
    items: list[dict]   # {name, result, unit, ref}


@router.post("/save")
def save_result(payload: SaveRequest):
    try:
        save_items(payload.date, payload.time, payload.dialysis, payload.items)
    except ValueError as e:
        # 日付や時刻がおかしいまま保存すると後から気付きにくい。
        # 400で返してワークフローを失敗させ、その場で分かるようにする
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "saved": len(payload.items)}
