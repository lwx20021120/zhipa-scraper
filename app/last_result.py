# -*- coding: utf-8 -*-
"""上次结果持久化：把最近一次成功抓取的数据存到磁盘。

解决 web 模式刷新页面（F5）/ 应用重启后内存中的 last_rows 丢失问题：
启动时自动恢复上次的数据预览，避免"刷新一下就白抓了"。
"""
import json
from pathlib import Path

LAST_RESULT_FILE = Path(__file__).resolve().parent.parent / ".last_result.json"
# 预览只存前 500 行（避免文件过大），总数单独记录
MAX_PREVIEW_ROWS = 500


def save_last_result(rows: list, fields: list = None) -> None:
    """保存最近一次抓取结果（供刷新/重启后恢复预览）。"""
    if not rows:
        return
    try:
        data = {
            "rows": rows[:MAX_PREVIEW_ROWS],
            "fields": fields or [],
            "total": len(rows),
        }
        LAST_RESULT_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, TypeError):
        pass


def load_last_result() -> tuple:
    """读取上次结果。返回 (rows, fields, total)；无则 (None, None, 0)。"""
    if not LAST_RESULT_FILE.exists():
        return None, None, 0
    try:
        data = json.loads(LAST_RESULT_FILE.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        if not rows:
            return None, None, 0
        return rows, data.get("fields") or [], data.get("total", len(rows))
    except (json.JSONDecodeError, OSError):
        return None, None, 0


def clear_last_result() -> None:
    """清空上次结果（可选：用户手动清空时用）。"""
    try:
        if LAST_RESULT_FILE.exists():
            LAST_RESULT_FILE.unlink()
    except OSError:
        pass
