# -*- coding: utf-8 -*-
"""検査結果をDBに保存するロジック(FastAPIに依存しない)"""
import os
import re
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lab_results.db")

CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS lab_results (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_date     TEXT NOT NULL,   -- 検査日
        exam_time     TEXT NOT NULL,   -- 時刻
        dialysis_type TEXT,            -- 処置区分(処置前/処置後)
        name          TEXT NOT NULL,   -- 項目名
        result        REAL,            -- 検査結果(数値。数値化できなければNULL)
        result_text   TEXT,            -- 検査結果(帳票の原文。「陰性」「0.05以下」など)
        unit          TEXT,            -- 単位
        reference     TEXT,            -- 基準値
        UNIQUE(exam_date, exam_time, name)
    )
"""

# 旧スキーマ(日本語列名)からの対応。値が無い列はNULLで埋める
RENAME_FROM = {
    "exam_date": "検査日",
    "exam_time": "時刻",
    "dialysis_type": "処置区分",
    "name": "項目名",
    "result": "検査結果",
    "result_text": "検査結果_原文",
    "unit": "単位",
    "reference": "基準値",
}


def migrate_to_english(conn, columns):
    """日本語列名のテーブルを英字列名へ作り替える(データは引き継ぐ)"""
    targets = list(RENAME_FROM)
    sources = [RENAME_FROM[t] if RENAME_FROM[t] in columns else "NULL" for t in targets]

    conn.execute(CREATE_TABLE.replace("lab_results", "lab_results_new"))
    conn.execute(
        f"INSERT INTO lab_results_new ({', '.join(targets)}) "
        f"SELECT {', '.join(sources)} FROM lab_results"
    )
    conn.execute("DROP TABLE lab_results")
    conn.execute("ALTER TABLE lab_results_new RENAME TO lab_results")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(lab_results)")]
        if "検査日" in columns:
            migrate_to_english(conn, columns)   # 旧スキーマなら作り替える
        else:
            conn.execute(CREATE_TABLE)          # 無ければ作る。既に英字ならそのまま
        conn.commit()
    finally:
        conn.close()


init_db()


def normalize_date(raw: str) -> str:
    # "26/6/15" も "2026/6/15" も "2026-06-15" に統一する
    parts = raw.strip().split("/")
    if len(parts) != 3:
        return raw  # 想定外の形式はそのまま返す(データを消さない)
    y, m, d = parts
    if len(y) == 2:
        y = "20" + y
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except ValueError:
        return raw


def normalize_time(raw: str) -> str:
    # "5:00" も "05:00" も "05:00" に統一する
    parts = raw.strip().split(":")
    if len(parts) == 2:
        h, m = parts
        return f"{int(h):02d}:{int(m):02d}"
    elif len(parts) == 3:
        h, m, s = parts
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    else:
        return raw  # 想定外の形式はそのまま返す(データを消さない)


# 検査日として遡りうる年数。これより古ければ検査日ではないと判断する
MAX_YEARS_BACK = 20


def validate_exam_date(normalized: str) -> None:
    """検査日として妥当かを確かめる。おかしければValueErrorを投げて保存を止める。

    帳票の日付行はOCRが読めないことがあり(3ページ目で実際に発生)、読む材料が
    無いとLLMが生年月日を検査日として拾う。'65/12/29生 を拾って 0006-12-29 として
    保存された事例があるため、静かに通さず落とす
    """
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"検査日として解釈できない値です: {normalized!r}")

    today = datetime.now()
    if parsed.date() > today.date():
        raise ValueError(f"検査日が未来の日付です: {normalized}")
    if parsed.year < today.year - MAX_YEARS_BACK:
        raise ValueError(f"検査日が古すぎます(生年月日を拾った可能性): {normalized}")


def validate_exam_time(normalized: str) -> None:
    """時刻が空だったり形式が崩れていれば保存を止める。
    日付を取り違えたときは時刻も一緒に落ちていることが多く、異常の手掛かりになる"""
    if not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", normalized):
        raise ValueError(f"検査時刻として解釈できない値です: {normalized!r}")


# LLMは帳票の表記をそのまま転記する方針のため、保存直前のここで正規化する
DIALYSIS_TYPE_MAP = {"透析前": "処置前", "透析後": "処置後"}


def normalize_dialysis_type(raw):
    return DIALYSIS_TYPE_MAP.get(raw, raw)


def parse_value(raw):
    # 数値型・文字列・Noneのどれで来ても扱えるようにする
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = re.match(r"[\d.]+", str(raw).strip())
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def save_items(date: str, time: str, dialysis_type: str, items: list[dict]):
    """正規化前のdate/timeを受け取り、DBへ保存する"""
    date = normalize_date(date)
    time = normalize_time(time)
    dialysis_type = normalize_dialysis_type(dialysis_type)
    validate_exam_date(date)          # 誤った日付で保存すると後から気付きにくいので、ここで止める
    validate_exam_time(time)
    conn = sqlite3.connect(DB_PATH)   # 呼び出しごとに新しい接続を開く(スレッド安全のため)
    try:
        for item in items:
            raw = item.get("result")          # キーが無くても落ちないようにする
            conn.execute(
                "INSERT OR REPLACE INTO lab_results "
                "(exam_date, exam_time, dialysis_type, name, result, result_text, unit, reference) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (date, time, dialysis_type, item.get("name", ""), parse_value(raw),
                 "" if raw is None else str(raw), item.get("unit", ""),
                 item.get("ref", "")),
            )
        conn.commit()
    finally:
        conn.close()   # 例外が起きても必ず閉じる(DBロックの残留を防ぐ)
