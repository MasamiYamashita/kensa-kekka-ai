# -*- coding: utf-8 -*-
from fastapi import FastAPI, File, UploadFile
from rapidocr import RapidOCR
import cv2
import numpy as np
import re
from save_api import router as save_router  # 別ファイルからrouterを持ってくる
from trend_api import router as trend_router
from backup_api import router as backup_router
from char_map import CHAR_MAP
from word_map import WORD_MAP

app = FastAPI()
app.include_router(save_router) # DB処理を行うrouterをFastAPIに組み込む
app.include_router(trend_router) # 推移グラフ・サマリーを返すrouterを組み込む
app.include_router(backup_router) # Google Driveバックアップを行うrouterを組み込む

# RapidOCRの初期化
engine = RapidOCR(params={
    "Rec.lang_type": "japan",   # 日本語認識を有効にする
    "Det.unclip_ratio": 1.6,    # 既定は1.6〜2.0程度。下げると行同士が誤って繋がりにくくなる
})

# 表のヘッダー。この4つのx座標を列の基準にする
HEADER_LABELS = ("検査項目", "検査結果", "単位", "基準値")


def find_header(lines):
    """ヘッダー行を探し、(行番号, 各列のx中心) を返す。見つからなければ (None, None)"""
    for index, line in enumerate(lines):
        found = {}
        for it in line:
            text = it["text"].strip()
            if text in HEADER_LABELS and text not in found:
                found[text] = (it["x0"] + it["x1"]) / 2
        if len(found) == len(HEADER_LABELS):
            return index, [found[label] for label in HEADER_LABELS]
    return None, None


def column_bounds(centers):
    """隣り合う列中心の中点を境界にして、列ごとの(左端, 右端)を作る"""
    bounds = []
    for i, center in enumerate(centers):
        left = float("-inf") if i == 0 else (centers[i - 1] + center) / 2
        right = float("inf") if i == len(centers) - 1 else (center + centers[i + 1]) / 2
        bounds.append((left, right))
    return bounds


def assign_columns(line, bounds):
    """各セルを、重なりが最大の列へ割り当てる。欠けた列は空文字のまま残す"""
    columns = [[] for _ in bounds]
    for it in line:
        best, best_overlap = 0, None
        for i, (left, right) in enumerate(bounds):
            overlap = min(it["x1"], right) - max(it["x0"], left)
            if best_overlap is None or overlap > best_overlap:
                best, best_overlap = i, overlap
        columns[best].append(it["text"])
    return [" ".join(texts) for texts in columns]   # 同じ列に複数あればスペースで繋ぐ


@app.get("/")
def read_root():
    return {"message": "Hello, ocr!"}

@app.post("/ocr")
def upload_file(file: UploadFile = File(...)):
    # file.fileに実体が入ってくる
    data = file.file.read()  # 読み出すとポインタが末尾になる点に注意（再利用するならseek(0)が必要）
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    file.file.close()

    result = engine(img)

    # 行混じりのテキストを確認テスト
    # for box, text in zip(result.boxes, result.txts):
    #     if text in ("無機リン", "尿素空素", "尿酸", "CRP定量"):
    #         ys = [p[1] for p in box]
    #         print(text, "cy=", sum(ys)/4, "height=", max(ys)-min(ys))

    items = []

    for box, text in zip(result.boxes, result.txts):
        ys = [p[1] for p in box]          # 4点のy座標
        xs = [p[0] for p in box]          # 4点のx座標
        text = text.translate(CHAR_MAP)   # 中国文字を日本文字に変換
        for old, new in WORD_MAP.items():
            text = text.replace(old, new)  # 単語レベルの正規化
        items.append({
            "cy": sum(ys) / 4,            # y中心
            "x0": min(xs),                # 左端
            "x1": max(xs),                # 右端
            "h":  max(ys) - min(ys),      # 高さ
            "text": text,
        })

    items.sort(key=lambda it: it["cy"])   # y中心でソート

    lines = []          # 完成した行たち(行=itemのリスト)
    current = []        # いま作りかけの行

    for it in items:
        if not current:
            current.append(it)          # 最初の1個
        elif (abs(current[-1]["cy"] - it["cy"]) < sum(i["h"] for i in current) / len(current) * 0.5):
            # itのcyと、currentの平均の要素のcyの差(0.5倍の高さ)で判定
            current.append(it)
        else:
            lines.append(current)       # 行を確定して
            current = [it]              # 新しい行を始める

    lines.append(current)       # 最終行をlinesに追加

    lines = [sorted(line, key=lambda it: it["x0"]) for line in lines]   # 各行を左から順に並べる

    # ヘッダーが見つかれば、それ以降の行は列を復元して常に4列で返す。
    # 見つからなければ従来どおり、並び順のままタブで繋ぐ。
    header_index, centers = find_header(lines)
    bounds = column_bounds(centers) if centers else None

    result_lines = []
    for index, line in enumerate(lines):
        if bounds is not None and index > header_index:
            cells = assign_columns(line, bounds)
            # 項目名はDBの一意キーになる。OCRが入れる空白は行ごとに揺れるため取り除く
            cells[0] = re.sub(r"\s+", "", cells[0])
        else:
            cells = [it["text"] for it in line]
        result_lines.append("\t".join(cells))

    return "\n".join(result_lines)

