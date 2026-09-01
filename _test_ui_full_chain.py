# -*- coding: utf-8 -*-
"""前端 UI 全链路测试（mock flet + 真实后端）。

模拟用户在界面上：
1. 选择 crawl4ai 引擎
2. 输入 AI 指令 + URL
3. 点击「开始抓取」
4. 验证 _scrape_worker 真正调用 run_ai_scrape 且数据回显到表格

用真实后端（Crawl4AI 引擎真实抓取 books 站），只 mock flet 界面层。
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")

import types as _t
import json


def _make_flet():
    flet = _t.ModuleType("flet")
    flet.Page = type("Page", (), {})

    class _Colors:
        GREY_600 = "grey600"; ORANGE_700 = "orange700"
        BLUE = "blue"; GREY_800 = "grey800"
    flet.Colors = _Colors

    class _FontWeight:
        BOLD = "bold"; W_500 = "w500"
    flet.FontWeight = _FontWeight

    class _TextOverflow:
        ELLIPSIS = "ellipsis"
    flet.TextOverflow = _TextOverflow

    class _ScrollMode:
        AUTO = "auto"
    flet.ScrollMode = _ScrollMode

    flet.Text = lambda *a, **kw: _t.SimpleNamespace(
        value=kw.get("value", a[0] if a else ""), visible=kw.get("visible", True),
        color=kw.get("color"), size=kw.get("size"), max_lines=kw.get("max_lines"),
        overflow=kw.get("overflow"), no_wrap=kw.get("no_wrap"),
        weight=kw.get("weight"))
    flet.Column = lambda controls=None, **kw: _t.SimpleNamespace(
        controls=controls or [], scroll=kw.get("scroll"),
        height=kw.get("height"), spacing=kw.get("spacing"), visible=kw.get("visible", True))
    flet.DataTable = lambda **kw: _t.SimpleNamespace(
        columns=[], rows=[], visible=kw.get("visible", False))
    flet.DataColumn = lambda c: c
    flet.DataRow = lambda cells: cells
    flet.DataCell = lambda c: c
    flet.Container = lambda **kw: _t.SimpleNamespace(**kw)
    flet.Row = lambda *a, **kw: _t.SimpleNamespace(
        controls=(list(a[0]) if a and isinstance(a[0], list)
                  else list(a) if a else kw.get("controls", [])), **kw)
    flet.Dropdown = lambda **kw: _t.SimpleNamespace(**kw)
    flet.DropdownOption = lambda **kw: kw
    flet.TextField = lambda **kw: _t.SimpleNamespace(
        value=kw.pop("value", ""), disabled=kw.pop("disabled", False), **kw)
    flet.ElevatedButton = lambda *a, **kw: _t.SimpleNamespace(disabled=False, **kw)
    flet.IconButton = lambda *a, **kw: _t.SimpleNamespace(**kw)
    flet.Checkbox = lambda **kw: _t.SimpleNamespace(**kw)
    flet.ProgressRing = lambda **kw: _t.SimpleNamespace(**kw)
    flet.SnackBar = lambda **kw: _t.SimpleNamespace(open=False)
    flet.FilePicker = lambda **kw: _t.SimpleNamespace()
    flet.Divider = lambda **kw: _t.SimpleNamespace()
    flet.AlertDialog = lambda **kw: _t.SimpleNamespace()
    flet.ListTile = lambda **kw: _t.SimpleNamespace()
    flet.Icons = _t.SimpleNamespace(ADD="add", PLAY_ARROW="play", SETTINGS="s",
                                    HISTORY="h", DELETE_OUTLINE="del")
    flet.Link = lambda **kw: _t.SimpleNamespace(**kw)
    return flet


class _Page:
    def __init__(self):
        self.overlay = []
        self.updates = 0

    def update(self):
        self.updates += 1

    def add(self, *a):
        pass

    def show_dialog(self, *a):
        pass

    def pop_dialog(self, *a):
        pass

    def run_task(self, *a, **kw):
        pass


def run_ui_test():
    import flet as ft
    sys.modules["flet"] = _make_flet()
    from app.ui.main_view import MainView

    page = _Page()
    mv = MainView(page)

    # 模拟用户在界面上的操作
    mv.engine_select.value = "crawl4ai"          # 选 Crawl4AI 引擎
    mv.ai_input.value = "提取页面上所有书籍的标题、价格和链接"
    mv.url_input.value = "https://books.toscrape.com/"
    mv.headless_checkbox.value = True
    mv.pg_deep.value = "0"

    # 模拟点击「开始抓取」
    mv._on_start(None)

    # 等待后台线程完成（轮询）
    for _ in range(120):
        if not mv.start_btn.disabled:
            break
        import time
        time.sleep(1)

    assert mv.last_rows, "应抓到数据"
    assert mv.data_table.visible, "表格应可见"
    print(f"  结果: {len(mv.last_rows)} 行")
    print(f"  状态: {mv.status_text.value}")
    print(f"  首行: {list(mv.last_rows[0].items())[:3]}")
    assert len(mv.last_rows) >= 10, "应至少 10 行"
    print("  ✅ UI 全链路：选引擎→输指令→开始→数据回显表格")
    return True


def run_ui_deep_test():
    """UI 整站深爬场景：auto 引擎 + 整站指令 → Crawl4AI BFS 深爬。"""
    import flet as ft
    sys.modules["flet"] = _make_flet()
    from app.ui.main_view import MainView

    page = _Page()
    mv = MainView(page)

    # 模拟：auto 引擎 + 整站指令 + 深爬页数设置
    mv.engine_select.value = "auto"
    mv.ai_input.value = "爬取这个网站的所有分类页面，提取书籍标题和价格"
    mv.url_input.value = "https://books.toscrape.com/"
    mv.headless_checkbox.value = True
    mv.pg_deep.value = "2"   # 深爬深度 2

    mv._on_start(None)

    import time
    for _ in range(240):
        if not mv.start_btn.disabled:
            break
        time.sleep(1)

    assert mv.last_rows, "应抓到数据"
    print(f"  整站深爬结果: {len(mv.last_rows)} 行")
    print(f"  状态: {mv.status_text.value}")
    assert len(mv.last_rows) > 100, "整站深爬应 >100 行"
    print("  ✅ UI 整站深爬：auto 引擎自动走 Crawl4AI BFS")
    return True


if __name__ == "__main__":
    print("=== 前端 UI 全链路测试（真实后端）===")
    ok1 = run_ui_test()
    ok2 = run_ui_deep_test()
    ok = ok1 and ok2
    print("\n结论:", "✅ 全部通过" if ok else "❌ 失败")
    sys.exit(0 if ok else 1)
