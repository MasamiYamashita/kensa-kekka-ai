# -*- coding: utf-8 -*-
from fastapi import APIRouter
from pydantic import BaseModel
from save import save_items

router = APIRouter()


class SaveRequest(BaseModel):
    date: str          # 採取日 "26/7/20"
    time: str          # 採取時刻 "21:00"
    dialysis: str = ""  # 透析前 / 透析後
    items: list[dict]   # {name, result, unit, ref}


@router.post("/save")
def save_result(payload: SaveRequest):
    save_items(payload.date, payload.time, payload.dialysis, payload.items)
    return {"status": "ok", "saved": len(payload.items)}
