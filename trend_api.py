# -*- coding: utf-8 -*-
import base64

from fastapi import APIRouter
from fastapi.responses import Response

from trend_graph import generate_trend_data

router = APIRouter()


@router.get("/trend")
def get_trend():
    image_base64, summary = generate_trend_data()
    return {"image_base64": image_base64, "summary": summary}


@router.get("/trend_image")
def get_trend_image():
    # ブラウザが直接<img>で読み込めるよう、base64ではなく画像バイナリそのものを返す
    image_base64, _ = generate_trend_data()
    return Response(content=base64.b64decode(image_base64), media_type="image/png")
