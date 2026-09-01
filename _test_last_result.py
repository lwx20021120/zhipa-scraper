# -*- coding: utf-8 -*-
"""验证「上次数据保留」逻辑：持久化 + 恢复 + 0 行不覆盖。"""
import sys
import json
from pathlib import Path

sys.path.insert(0, r"D:\workbuudy\Scrapling")

from app import last_result

TEST_ROWS = [{"标题": f"电影{i}", "评分": "9.0"} for i in range(230)]
TEST_FIELDS = [{"name": "标题", "selector": "h3", "type": "text"},
               {"name": "评分", "selector": ".rating", "type": "text"}]


def t1_save_and_load_roundtrip():
    """保存 → 读取 → 内容一致，且截断到 500 行。"""
    last_result.save_last_result(TEST_ROWS, TEST_FIELDS)
    rows, fields, total = last_result.load_last_result()
    assert rows == TEST_ROWS, "roundtrip 行不一致"
    assert fields == TEST_FIELDS, "roundtrip 字段不一致"
    assert total == 230, f"total 应为 230，实际 {total}"
    assert len(rows) == 230, "230 < 500 不应截断"
    # 超过 500 行截断
    big = [{"标题": f"x{i}"} for i in range(800)]
    last_result.save_last_result(big, [])
    rows2, _, total2 = last_result.load_last_result()
    assert len(rows2) == 500, f"应截断到 500，实际 {len(rows2)}"
    assert total2 == 800, f"total 应保留 800，实际 {total2}"
    print("  t1 保存/读取/截断 ✅")
    return True


def t2_empty_rows_do_not_override():
    """空行不覆盖上次结果（模拟 UI 里 rows=[] 时）。"""
    last_result.save_last_result(TEST_ROWS, TEST_FIELDS)
    last_result.save_last_result([], [])  # 模拟 0 行结果
    rows, _, total = last_result.load_last_result()
    assert rows == TEST_ROWS, "空结果不应覆盖上次数据"
    assert total == 230
    print("  t2 空行不覆盖 ✅")
    return True


def t3_clear_works():
    """清空逻辑正常。"""
    last_result.save_last_result(TEST_ROWS, TEST_FIELDS)
    assert last_result.load_last_result()[0] is not None
    last_result.clear_last_result()
    rows, _, _ = last_result.load_last_result()
    assert rows is None, "清空后应无数据"
    print("  t3 清空 ✅")
    return True


def t4_restore_hint_logic():
    """验证 _render_table 的 preview_note 逻辑（通过直接调用 UI 方法需要 flet，
    这里验证数据层面的 total 语义即可）。"""
    # save_last_result 里 total 应正确反映总行数
    last_result.save_last_result(TEST_ROWS[:200], TEST_FIELDS)
    _, _, total = last_result.load_last_result()
    assert total == 200
    print("  t4 total 语义 ✅")
    return True


if __name__ == "__main__":
    ok = all([t1_save_and_load_roundtrip(),
              t2_empty_rows_do_not_override(),
              t3_clear_works(),
              t4_restore_hint_logic()])
    last_result.clear_last_result()  # 清理测试残留
    print("\n结论:", "✅ 全部通过" if ok else "❌ 有失败")
    sys.exit(0 if ok else 1)
