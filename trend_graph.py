# -*- coding: utf-8 -*-
"""lab_results.db から指定項目の推移グラフ(PNG)を作成する"""
import base64
import os
import re
import sqlite3
import textwrap
from datetime import datetime, timedelta
from io import BytesIO

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

matplotlib.rcParams["font.family"] = "Meiryo"  # 日本語表示のため

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lab_results.db")
N8N_DB_PATH = os.path.join(BASE_DIR, "lab_results_n8n.db")
OUTPUT_PATH = os.path.join(BASE_DIR, "trend_graph.png")

# 推移を見たい項目(必要に応じてここを編集する)
TARGET_ITEMS = [
    "尿素窒素", "クレアチニン", "カリウム",
    "リン", "カルシウム", "ヘモグロビン", "ヘマトクリット",
    "PTH", "アルブミン", "フェリチン", "TSAT",
]

# 対象者向けの管理目標値(下限, 上限)。関連学会ガイドラインによる。
#
#   骨・ミネラル代謝異常の診療ガイドライン 2025年改訂版
#   (学会誌 59(4):127-224, 2026)
#     Statement 3.1.2  血清P値      3.5 mg/dL以上 5.5 mg/dL未満
#     Statement 3.2.1  血清補正Ca値  8.4 mg/dL以上 9.5 mg/dL未満
#     Statement 4.1.1  intact PTH  240 pg/mL未満の範囲で症例毎に個別化
#
#   貧血治療のガイドライン 2015年版
#   (学会誌 49(2):89-158, 2016)
#     CQ1  対象者のHb値  週初めの採血で 10 g/dL以上 12 g/dL未満
#
# 帳票の基準値は健常者向けのため、対象者ではこちらを優先して判定する。
# いずれも集団に対する目標値であり、個別の目標は主治医が定める。
DIALYSIS_TARGETS = {
    "リン":       (3.5, 5.5),
    "カルシウム":  (8.4, 9.5),
    "PTH":       (None, 240.0),
    "ヘモグロビン": (10.0, 12.0),
}

# ガイドラインが補正Ca値での判定を求めている項目。
# アルブミンが4.0 g/dL未満の場合は補正が必要だが、本ツールは補正前の値で判定している。
NEEDS_CORRECTION = ("カルシウム",)

# 略号の正式名称。帳票には略号しか印字されないため、ここで補う。
# LLMに言い換えを生成させたところ「心房ナトリウラーゼペプチド」「パラジアルドスタチン」
# のような存在しない語を作ったため、確定した知識としてこちらから渡す。
# 学会目標値と同様に帳票外の知識なので、出所を1箇所にまとめる意図でここに置く。
FULL_NAMES = {
    "PTH":   "副甲状腺ホルモン",
    "h-ANP": "心房性ナトリウム利尿ペプチド",
    "TIBC":  "総鉄結合能",
    "TSAT":  "トランスフェリン飽和度",
    "UIBC":  "不飽和鉄結合能",
    "MCV":   "平均赤血球容積",
    "MCH":   "平均赤血球ヘモグロビン量",
    "MCHC":  "平均赤血球ヘモグロビン濃度",
}


def is_out_of_range(low, high, value):
    return (low is not None and value < low) or (high is not None and value > high)


def parse_reference(raw: str):
    """基準値の文字列から (下限, 上限) を取り出す。片方だけの場合は None。"""
    if not raw:
        return None, None
    m = re.match(r"([\d.]+)\s*[~〜]\s*([\d.]+)", raw)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"([\d.]+)\s*以下", raw)
    if m:
        return None, float(m.group(1))
    m = re.match(r"([\d.]+)\s*以上", raw)
    if m:
        return float(m.group(1)), None
    return None, None


MARKERS = {"処置前": "o", "処置後": "^"}


def fetch_item(conn, name):
    rows = conn.execute(
        "SELECT exam_date, result, reference, dialysis_type FROM lab_results "
        "WHERE name = ? AND result IS NOT NULL ORDER BY exam_date",
        (name,),
    ).fetchall()

    dates, values, kinds, low, high = [], [], [], None, None
    for date_str, value, ref, kind in rows:
        dates.append(datetime.strptime(date_str, "%Y-%m-%d"))
        values.append(value)
        kinds.append(kind or "")
        if ref:
            l, h = parse_reference(ref)
            if l is not None and h is not None and l > h:
                continue  # OCR/LLMの誤読で下限>上限になった壊れた基準値は無視する
            # 日付順に見ているので、最新の妥当な基準値で上書きする(過去の誤読を引きずらない)
            if l is not None:
                low = l
            if h is not None:
                high = h

    # 対象者向けの目標値がある項目は、帳票の基準値(健常者向け)より優先する
    if name in DIALYSIS_TARGETS:
        low, high = DIALYSIS_TARGETS[name]
        source = "学会目標値"
    else:
        source = "帳票基準値"
    return dates, values, kinds, low, high, source


def plot_item(ax, name, dates, values, kinds, low, high, source, auto_added):
    ax.plot(dates, values, color="#a0aec0", linewidth=1.3, zorder=1)  # 線は薄いグレーで繋ぐだけ

    if low is not None or high is not None:
        y0 = low if low is not None else ax.get_ylim()[0]
        y1 = high if high is not None else ax.get_ylim()[1]
        ax.axhspan(y0, y1, color="#38a169", alpha=0.12, zorder=0)

    # 処置前=丸、処置後=三角。基準値の外にある点は赤く強調する
    for d, v, kind in zip(dates, values, kinds):
        out_of_range = is_out_of_range(low, high, v)
        marker = MARKERS.get(kind, "o")
        color = "#e53e3e" if out_of_range else "#2b6cb0"
        ax.plot(d, v, marker=marker, color=color, markersize=7 if kind == "処置後" else 5,
                 linestyle="none", zorder=5)

    if len(dates) == 1:
        # 点が1つだけだと日付軸のスケールが異常に広がるため、幅を明示的に固定する
        ax.set_xlim(dates[0] - timedelta(days=30), dates[0] + timedelta(days=30))

    ax.set_title(name, fontsize=12, fontweight="bold")
    captions = []
    if source == "学会目標値":
        # 帯が健常者の基準値ではなく対象者向けの目標値であることを示す
        captions.append("帯は学会の管理目標値")
    if auto_added:
        # 固定リストに無く、直近値が範囲外だったために自動で追加された項目であることを示す
        captions.append("直近値が範囲外のため自動表示")
    if captions:
        ax.set_xlabel(" / ".join(captions), fontsize=7, color="#4a5568")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.grid(True, alpha=0.3)

    # 凡例は点の色(基準値の内外)に左右されない固定表示にする
    present_kinds = [k for k in MARKERS if k in kinds]
    if len(present_kinds) > 1:
        handles = [
            Line2D([0], [0], marker=MARKERS[k], color="#4a5568", linestyle="none",
                    markersize=7 if k == "処置後" else 5, label=k)
            for k in present_kinds
        ]
        ax.legend(handles=handles, fontsize=7, loc="best")


def collect_items_data(conn):
    """固定項目(TARGET_ITEMS)に加え、固定項目に無い項目のうち
    直近値が判定基準の範囲外だったものを自動で追加する。
    判定基準(目標値・帳票基準値のいずれも)が無い項目は、判定できないため対象にしない。
    """
    items_data = []
    seen = set(TARGET_ITEMS)

    for name in sorted(TARGET_ITEMS):
        dates, values, kinds, low, high, source = fetch_item(conn, name)
        if dates:
            items_data.append((name, dates, values, kinds, low, high, source, False))

    all_names = [row[0] for row in conn.execute(
        "SELECT DISTINCT name FROM lab_results WHERE result IS NOT NULL"
    )]
    for name in sorted(all_names):
        if name in seen:
            continue
        dates, values, kinds, low, high, source = fetch_item(conn, name)
        if not dates or (low is None and high is None):
            continue
        if is_out_of_range(low, high, values[-1]):
            items_data.append((name, dates, values, kinds, low, high, source, True))

    return items_data


def collect_items_data_n8n(conn):
    """n8n監視用: 「常に見せる固定パネル」を持たない。TARGET_ITEMSのような
    特定領域の項目セットに偏らないよう、診療科を問わず全項目から
    その時点で判定基準に照らして範囲外だったものだけを対象にする"""
    items_data = []
    all_names = [row[0] for row in conn.execute(
        "SELECT DISTINCT name FROM lab_results WHERE result IS NOT NULL"
    )]
    for name in sorted(all_names):
        dates, values, kinds, low, high, source = fetch_item(conn, name)
        if not dates or (low is None and high is None):
            continue
        if is_out_of_range(low, high, values[-1]):
            items_data.append((name, dates, values, kinds, low, high, source, True))

    return items_data


def build_figure(items_data):
    cols = 3
    rows = (len(items_data) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 3.5 * rows))
    axes = axes.flatten() if len(items_data) > 1 else [axes]

    for ax, (name, dates, values, kinds, low, high, source, auto_added) in zip(axes, items_data):
        plot_item(ax, name, dates, values, kinds, low, high, source, auto_added)

    for ax in axes[len(items_data):]:
        ax.axis("off")

    fig.suptitle("検査結果の推移", fontsize=16, fontweight="bold", y=0.99)
    fig.text(0.5, 0.955, "○ 処置前　△ 処置後　赤色は目標範囲の外(右下は自動検出項目)",
             ha="center", fontsize=10, color="#4a5568")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    return fig


def build_summary(items_data):
    """各項目の最新値・目標値との比較・前回との傾向をLLMに渡しやすい形でまとめる"""
    summary = []
    for name, dates, values, kinds, low, high, source, auto_added in items_data:
        latest_value = values[-1]

        if is_out_of_range(low, high, latest_value):
            judgement = "目標範囲より低い" if low is not None and latest_value < low else "目標範囲より高い"
        else:
            judgement = "範囲内"

        trend = None
        if len(values) >= 2:
            prev = values[-2]
            if latest_value > prev:
                trend = "上昇"
            elif latest_value < prev:
                trend = "低下"
            else:
                trend = "変化なし"

        if low is not None and high is not None:
            ref = f"{low}~{high}"
        elif high is not None:
            ref = f"{high}以下"
        elif low is not None:
            ref = f"{low}以上"
        else:
            ref = None

        row = {
            "項目名": name,
            "検査日": dates[-1].strftime("%Y-%m-%d"),
            "検査結果": latest_value,
            "目標値": ref,
            "目標値の種別": source,
            "判定": judgement,
            "前回との比較": trend,
            "固定リスト外の自動検出": auto_added,
        }
        if name in FULL_NAMES:
            row["正式名称"] = FULL_NAMES[name]
        if name in NEEDS_CORRECTION:
            # ガイドラインは補正Ca値での判定を求めているが、ここでは補正前の値を使っている
            row["注記"] = "アルブミンによる補正前の値。本来は補正Ca値で判定する"
        summary.append(row)
    return summary


def generate_trend_data():
    """APIから呼ぶ用: (グラフ画像のbase64文字列, サマリーのリスト) を返す"""
    conn = sqlite3.connect(DB_PATH)
    items_data = collect_items_data(conn)
    conn.close()

    fig = build_figure(items_data)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    image_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    summary = build_summary(items_data)
    return image_base64, summary


def is_chronic(values, low, high):
    """全履歴が一度も範囲内に入っていない項目は、今回固有の異常ではなく
    構造的に外れ続けている項目とみなす(特定の項目名に頼らない判定)"""
    return len(values) >= 2 and all(is_out_of_range(low, high, v) for v in values)


def build_n8n_summary(items_data):
    """build_summaryの結果に「慢性的に基準値外」フラグを付加する(n8n監視用)"""
    summary = build_summary(items_data)
    for row, (name, dates, values, kinds, low, high, source, auto_added) in zip(summary, items_data):
        row["慢性的に基準値外"] = is_chronic(values, low, high)
    return summary


def generate_n8n_data(db_path=N8N_DB_PATH):
    """n8n監視用: generate_trend_dataと同じ形だが、DBパスを指定でき、
    項目ごとに「慢性的に基準値外」フラグを付加する。既定はテスト用DB。
    collect_items_data_n8nを使うため、固定パネルを持たず範囲外の項目だけが対象になる"""
    conn = sqlite3.connect(db_path)
    items_data = collect_items_data_n8n(conn)
    conn.close()

    fig = build_figure(items_data)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    image_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    summary = build_n8n_summary(items_data)
    return image_base64, summary


COMMENT_LINES_PER_PAGE = 32  # A4・fontsize=11でページ下端まであふれずに収まる目安行数


def build_comment_pages(comment: str):
    """所見コメントをA4サイズの図として描画する(PDFの2ページ目以降用)。
    Markdownをレンダリングするのではなく、見出し記法だけプレーンテキスト向けに整形する。
    項目数が多いと1ページに収まらないため、あふれたら新しいページに送る"""
    plain = re.sub(r"^#+\s*", "", comment, flags=re.MULTILINE)
    blocks = [b for b in plain.split("\n\n") if b.strip()]

    wrapped_blocks = []
    for block in blocks:
        # break_on_hyphens=Falseにしないと「h-ANP」等がハイフンの位置で
        # 「h-」/「ANP」に割れてしまう。空白の無い日本語文はbreak_long_wordsで
        # 文字数ぎりぎりまで詰めて改行させる
        wrapped_blocks.append("\n".join(
            textwrap.fill(line, width=40, break_on_hyphens=False) if line else ""
            for line in block.split("\n")
        ))

    pages, current, used = [], [], 0
    for wb in wrapped_blocks:
        block_lines = wb.count("\n") + 2  # 本文行数 + 項目間の空行1行分
        if current and used + block_lines > COMMENT_LINES_PER_PAGE:
            pages.append("\n\n".join(current))
            current, used = [], 0
        current.append(wb)
        used += block_lines
    if current:
        pages.append("\n\n".join(current))

    figs = []
    for i, page_text in enumerate(pages):
        fig, ax = plt.subplots(figsize=(8.27, 11.69))  # A4縦
        ax.axis("off")
        title = "検査結果 所見" if i == 0 else f"検査結果 所見(続き {i + 1})"
        ax.text(0.06, 0.95, title, fontsize=18, fontweight="bold",
                 va="top", transform=ax.transAxes)
        ax.text(0.06, 0.88, page_text, fontsize=11, va="top", transform=ax.transAxes)
        figs.append(fig)
    return figs


def generate_n8n_report_pdf(comment: str, db_path=N8N_DB_PATH) -> tuple[bytes, str]:
    """n8n通知用: 推移グラフ(1ページ目)+所見コメント(2ページ目)のPDFを返す。
    ファイル名に使えるよう、レポートが対象とする最新の検査日(YYMMDD)も併せて返す"""
    conn = sqlite3.connect(db_path)
    items_data = collect_items_data_n8n(conn)
    conn.close()

    graph_fig = build_figure(items_data)
    comment_figs = build_comment_pages(comment)

    buf = BytesIO()
    with PdfPages(buf) as pdf:
        pdf.savefig(graph_fig)
        for fig in comment_figs:
            pdf.savefig(fig)
    plt.close(graph_fig)
    for fig in comment_figs:
        plt.close(fig)

    latest_dates = [dates[-1] for _, dates, *_ in items_data if dates]
    date_str = max(latest_dates).strftime("%y%m%d") if latest_dates else "unknown"

    return buf.getvalue(), date_str


def main():
    conn = sqlite3.connect(DB_PATH)
    items_data = collect_items_data(conn)
    conn.close()

    if not items_data:
        print("データが見つかりませんでした")
        return

    fig = build_figure(items_data)
    fig.savefig(OUTPUT_PATH, dpi=150)
    print(f"保存しました: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
