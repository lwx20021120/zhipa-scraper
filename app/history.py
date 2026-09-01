# -*- coding: utf-8 -*-
"""抓取历史：保存最近的成功抓取配置，支持一键重跑。"""
import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).resolve().parent.parent / "history.json"
MAX_ENTRIES = 30


def load_history() -> list:
    """读取历史记录（新→旧）。"""
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_entry(entry: dict) -> None:
    """保存一条历史（插入开头，超过上限截断）。"""
    history = load_history()
    entry = {k: v for k, v in entry.items() if v is not None}
    history.insert(0, entry)
    del history[MAX_ENTRIES:]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def build_entry(ai_input: str, url: str, fetcher: str, fields: list,
                pagination: dict, rows_count: int, used_fetcher: str = "") -> dict:
    """构造历史条目。"""
    return {
        "time": datetime.now().strftime("%m-%d %H:%M"),
        "ai_input": ai_input,
        "url": url,
        "fetcher": fetcher,
        "fields": fields,
        "pagination": pagination,
        "rows_count": rows_count,
        "used_fetcher": used_fetcher,
    }
