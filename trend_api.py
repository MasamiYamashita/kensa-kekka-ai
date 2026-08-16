# -*- coding: utf-8 -*-
import base64

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from trend_graph import generate_n8n_data, generate_n8n_report_pdf, generate_trend_data

router = APIRouter()


class ReportRequest(BaseModel):
    comment: str = ""


@router.get("/trend")
def get_trend():
    image_base64, summary = generate_trend_data()
    return {"image_base64": image_base64, "summary": summary}


@router.get("/trend_image")
def get_trend_image():
    # ブラウザが直接<img>で読み込めるよう、base64ではなく画像バイナリそのものを返す
    image_base64, _ = generate_trend_data()
    return Response(content=base64.b64decode(image_base64), media_type="image/png")


@router.get("/trend_n8n")
def get_trend_n8n():
    # n8n版(監視通知型)専用。テスト用DBを参照し、実データには触れない
    image_base64, summary = generate_n8n_data()
    return {"image_base64": image_base64, "summary": summary}


@router.get("/trend_n8n_image")
def get_trend_n8n_image():
    image_base64, _ = generate_n8n_data()
    return Response(content=base64.b64decode(image_base64), media_type="image/png")


@router.post("/report_n8n")
def post_report_n8n(payload: ReportRequest):
    # n8n通知用。推移グラフ+所見コメントの2ページPDFを返す
    pdf_bytes, date_str = generate_n8n_report_pdf(payload.comment)
    # Content-Dispositionを省略すると、n8nがURL末尾から拡張子なしのファイル名を
    # 推測してしまい、Discord等で開けなくなるため明示する。検査日を付けて識別しやすくする
    filename = f"report_n8n_{date_str}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
