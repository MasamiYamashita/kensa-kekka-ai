# -*- coding: utf-8 -*-
from fastapi import FastAPI, File, UploadFile
from rapidocr import RapidOCR
import cv2
import numpy as np
import re
import statistics
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


# 手持ち撮影の遠近ゆがみで、行は水平にならず傾く。しかも傾きは一定ではなく、
# ページ上部と下部で変わる(この帳票では上部+0.049〜下部-0.008)。
# そこで傾きを slope(y) = a + b*y の直線モデルで表し、打ち消してから行にまとめる。

def fit_line(samples):
    """(x, y)の並びに y = a + b*x を最小二乗で当てる。当てられなければNone"""
    n = len(samples)
    if n < 2:
        return None
    sx = sum(p[0] for p in samples)
    sy = sum(p[1] for p in samples)
    sxx = sum(p[0] ** 2 for p in samples)
    sxy = sum(p[0] * p[1] for p in samples)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:       # xが1点に潰れていると傾きが決まらない
        return None
    b = (n * sxy - sx * sy) / denom
    return (sy - b * sx) / n, b


def row_key(it, model):
    """傾きを打ち消したy。同じ行なら近い値になる"""
    a, b = model
    return it["cy"] - (a + b * it["cy"]) * it["cx"]


def skew_sharpness(group, slope, bin_size):
    """その傾きで投影したとき、行がどれだけ鋭く分離するか。大きいほど良い"""
    hist = {}
    for it in group:
        key = int((it["cy"] - slope * it["cx"]) / bin_size)
        hist[key] = hist.get(key, 0) + 1
    return sum(count * count for count in hist.values())


def estimate_skew(items, bin_size):
    """射影プロファイル法。ページをy方向の帯に分け、帯ごとに最も行が鋭く分離する
    傾きを総当たりで探し、その結果を slope(y) にまとめる。
    検出枠の角度から直接求める方法は、枠が水平寄りに歪むため精度が出なかった"""
    order = sorted(items, key=lambda it: it["cy"])
    bands = 5 if len(order) >= 20 else 1
    per = len(order) / bands
    measured = []
    for i in range(bands):
        group = order[int(i * per):int((i + 1) * per)]
        if not group:
            continue
        best, best_score = 0.0, -1
        for step in range(-240, 241):           # ±0.12(約±7度)を0.0005刻みで走査
            slope = step * 0.0005
            score = skew_sharpness(group, slope, bin_size)
            if score > best_score:
                best, best_score = slope, score
        measured.append((statistics.median(it["cy"] for it in group), best))
    if len(measured) == 1:
        return measured[0][1], 0.0              # 帯が1つならyによる変化は求められない
    return fit_line(measured) or (0.0, 0.0)


def refine_skew(rows, items, fallback):
    """組めた行から実際の傾きを測り直してモデルを作り直す。
    粗い推定でも大半の行は正しく組めるので、その行を物差しに使える"""
    span_min = (max(it["x1"] for it in items) - min(it["x0"] for it in items)) * 0.15
    measured = []
    for row in rows:
        xs = [it["cx"] for it in row]
        if len(row) >= 2 and max(xs) - min(xs) >= span_min:   # 横に広い行ほど傾きが正確に出る
            line = fit_line([(it["cx"], it["cy"]) for it in row])
            if line:
                measured.append((statistics.median(it["cy"] for it in row), line[1]))
    if len(measured) < 3:
        return fallback
    model = fit_line(measured)
    if model is None:
        return fallback
    a, b = model
    resid = [abs((a + b * y) - slope) for y, slope in measured]
    cut = statistics.median(resid) * 2.5
    kept = [p for p, r in zip(measured, resid) if r <= cut]   # 組み損ねた行の影響を落とす
    return fit_line(kept) or model


def group_rows(items, model, tolerance):
    """傾きを打ち消したyが近いものを同じ行にまとめる。
    比較相手は行の先頭に固定する。直前の1個と比べると、僅かな差が積み重なって
    行が数珠つなぎに結合してしまう"""
    keyed = sorted(((row_key(it, model), it) for it in items), key=lambda p: p[0])
    rows, current = [], []
    for key, it in keyed:
        if not current or abs(current[0][0] - key) < tolerance:
            current.append((key, it))
        else:
            rows.append([p[1] for p in current])
            current = [(key, it)]
    if current:
        rows.append([p[1] for p in current])
    return rows


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
            "cx": sum(xs) / 4,            # x中心(傾きの推定に使う)
            "x0": min(xs),                # 左端
            "x1": max(xs),                # 右端
            "h":  max(ys) - min(ys),      # 高さ
            "text": text,
        })

    if not items:
        return ""

    # 閾値はページ全体の文字高から一度だけ決める。行ごとに動く値だと基準がぶれる
    med_h = statistics.median(it["h"] for it in items)
    tolerance = med_h * 0.5

    # 傾きを粗く推定して一度行にまとめ、その行から傾きを測り直してもう一度まとめる。
    # 1回目だけでは、行の左端と右端でyが最大35pxずれて別の行に割れることがある
    model = estimate_skew(items, med_h / 4)
    model = refine_skew(group_rows(items, model, tolerance), items, model)
    lines = group_rows(items, model, tolerance)

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

