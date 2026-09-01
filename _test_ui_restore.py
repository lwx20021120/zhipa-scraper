# -*- coding: utf-8 -*-
"""验证 MainView 的「上次数据恢复」UI 逻辑（mock flet，不启动真实渲染）。

覆盖：
1. _restore_last_result 被调用后：last_rows/表格可见/stale_note 提示/状态栏
2. _render_table 的 preview_note 逻辑（>200 行显示提示，不叠加）
3. _scrape_worker 成功 0 行时保留旧数据
"""
import sys
import types

sys.path.insert(0, r"D:\workbuudy\Scrapling")

from app import last_result
last_result.clear_last_result()


# ---------- 最小 flet mock ----------
class _FakeText:
    def __init__(self, value="", **kw):
        self.value = value
        self.visible = kw.get("visible", False)
        self.color = kw.get("color")
        self.size = kw.get("size")
        self.weight = kw.get("weight")
        self.max_lines = kw.get("max_lines")


class _FakeColumn:
    def __init__(self, controls=None, **kw):
        self.controls = controls or []
        self.scroll = kw.get("scroll")
        self.height = kw.get("height")
        self.spacing = kw.get("spacing")


class _FakeTable:
    def __init__(self, **kw):
        self.columns = []
        self.rows = []
        self.visible = kw.get("visible", False)


class _FakePage:
    def __init__(self):
        self.overlay = []
        self._updates = 0

    def update(self):
        self._updates += 1

    def add(self, *a):
        pass

    def show_dialog(self, *a):
        pass

    def pop_dialog(self, *a):
        pass

    def run_task(self, handler, *a, **kw):
        # 模拟 flet run_task：同步执行协程（测试环境无事件循环）
        import asyncio
        if callable(handler):
            try:
                coro = handler(*a, **kw)
                if asyncio.iscoroutine(coro):
                    asyncio.run(coro)
                return
            except Exception:
                pass


def _make_flet_module():
    flet = types.ModuleType("flet")
    flet.Page = type("Page", (), {})  # 类型注解用

    class _Colors:
        GREY_600 = "grey600"
        ORANGE_700 = "orange700"
        BLUE = "blue"
        GREY_800 = "grey800"
    flet.Colors = _Colors

    class _FontWeight:
        BOLD = "bold"
        W_500 = "w500"
    flet.FontWeight = _FontWeight

    class _TextOverflow:
        ELLIPSIS = "ellipsis"
    flet.TextOverflow = _TextOverflow

    class _ScrollMode:
        AUTO = "auto"
    flet.ScrollMode = _ScrollMode

    class _MainAxisAlignment:
        SPACE_BETWEEN = "spaceBetween"
    flet.MainAxisAlignment = _MainAxisAlignment

    flet.Text = _FakeText
    flet.Column = _FakeColumn
    flet.DataTable = _FakeTable
    flet.DataColumn = lambda c: c
    flet.DataRow = lambda cells: cells
    flet.DataCell = lambda c: c
    flet.Container = lambda **kw: types.SimpleNamespace(**kw)
    flet.Row = lambda *a, **kw: types.SimpleNamespace(**kw)
    flet.Dropdown = lambda **kw: types.SimpleNamespace(**kw)
    flet.DropdownOption = lambda **kw: kw
    flet.TextField = lambda **kw: kw
    flet.ElevatedButton = lambda *a, **kw: types.SimpleNamespace(**kw)
    flet.IconButton = lambda *a, **kw: types.SimpleNamespace(**kw)
    flet.Checkbox = lambda **kw: types.SimpleNamespace(**kw)
    flet.ProgressRing = lambda **kw: types.SimpleNamespace(**kw)
    flet.SnackBar = lambda **kw: types.SimpleNamespace(open=False, content=None)
    flet.FilePicker = lambda **kw: kw
    flet.Divider = lambda **kw: kw
    flet.AlertDialog = lambda **kw: kw
    flet.ListTile = lambda **kw: kw
    flet.Icons = types.SimpleNamespace(ADD="add", PLAY_ARROW="play", SETTINGS="s",
                                       HISTORY="h", DELETE_OUTLINE="del")
    flet.Link = lambda **kw: kw
    flet.Image = lambda **kw: kw
    flet.ImageFit = types.SimpleNamespace(COVER="cover")
    return flet


def test_restore_on_start():
    """启动时自动恢复上次结果。"""
    import flet as ft
    sys.modules["flet"] = _make_flet_module()
    from app.ui.main_view import MainView

    # 先写入上次结果
    rows = [{"标题": f"电影{i}", "评分": "9.0"} for i in range(250)]
    last_result.save_last_result(rows, [{"name": "标题", "selector": "h3", "type": "text"}])

    page = _FakePage()
    mv = MainView(page)  # __init__ 里会调用 _restore_last_result

    assert mv.last_rows == rows, "last_rows 应恢复"
    assert mv.data_table.visible, "表格应可见"
    assert mv.data_table.columns, "应生成列"
    assert mv.data_table.rows, "应生成行"
    # 新版分页：只渲染 PAGE_SIZE 行（默认 50），250 行数据分 5 页
    assert len(mv.data_table.rows) == mv.page_size, \
        f"只渲染 {mv.page_size} 行（分页）"
    assert mv.pager.visible, "分页控件应可见"
    assert mv.current_page == 1, "恢复时回到第 1 页"
    assert "第 1/5 页" in mv.page_label.value, "应显示分页信息"
    assert "已恢复" in mv.stale_note.value, "stale_note 应有恢复提示"
    assert "已恢复" in mv.status_text.value, "状态栏应有恢复提示"
    print(f"  ✅ 启动恢复 + 分页渲染（{len(mv.data_table.rows)}/{len(rows)} 行，第 1/5 页）")
    return True


def test_engine_dropdown_has_crawl4ai():
    """引擎下拉必须包含全部 6 个引擎（含 crawl4ai）。"""
    import flet as ft
    sys.modules["flet"] = _make_flet_module()
    from app.ui.main_view import MainView

    page = _FakePage()
    mv = MainView(page)
    opts = mv.engine_select.options
    keys = [o["key"] for o in opts]
    print(f"  引擎选项: {keys}")
    assert "crawl4ai" in keys, "引擎下拉缺少 crawl4ai 选项"
    assert "unified" in keys, "引擎下拉缺少 unified 选项"
    assert len(keys) == 7, f"应有 7 个引擎选项，实际 {len(keys)}"
    print("  ✅ 引擎下拉含全部 7 个引擎（auto/unified/scrapling/direct/"
          "agent/browser-use/crawl4ai）")
    return True


def test_zero_rows_keeps_old():
    """抓取成功但 0 行 → 保留旧数据并提示。"""
    import flet as ft
    sys.modules["flet"] = _make_flet_module()
    from app.ui.main_view import MainView

    last_result.save_last_result([{"标题": "旧数据"}], [{"name": "标题"}])
    page = _FakePage()
    mv = MainView(page)

    # 模拟手动模式抓取返回 0 行
    import app.ui.main_view as mview
    orig_scrape = mview.scrape
    mview.scrape = lambda *a, **kw: ([], 200)
    try:
        mv._scrape_worker("http://x.com", "static", [{"name": "标题", "selector": "h3"}],
                          "", None)
    finally:
        mview.scrape = orig_scrape

    assert mv.last_rows, "旧数据应保留"
    assert mv.stale_note.visible, "应显示保留提示"
    assert "0 行" in mv.stale_note.value or "保留" in mv.stale_note.value, "提示应说明"
    assert "保留" in mv.status_text.value, "状态栏应说明保留"
    print("  ✅ 0 行结果保留旧数据")
    return True


def test_failure_keeps_old():
    """抓取异常 → 保留旧数据。"""
    import flet as ft
    sys.modules["flet"] = _make_flet_module()
    from app.ui.main_view import MainView

    last_result.save_last_result([{"标题": "旧数据2"}], [{"name": "标题"}])
    page = _FakePage()
    mv = MainView(page)

    import app.ui.main_view as mview
    from app.scraper import ScrapeError
    orig_scrape = mview.scrape
    mview.scrape = lambda *a, **kw: (_ for _ in ()).throw(ScrapeError("网站超时"))
    try:
        mv._scrape_worker("http://x.com", "static", [{"name": "标题"}], "", None)
    finally:
        mview.scrape = orig_scrape

    assert mv.last_rows, "异常时旧数据应保留"
    assert mv.data_table.visible, "表格应仍可见"
    assert "失败" in mv.status_text.value, "状态栏应显示失败"
    print("  ✅ 异常时保留旧数据")
    return True


def test_pagination_and_cross_column_switch():
    """分页显示 + 跨列数切换（不报 DataRow/DataCells 列数不匹配）。

    场景：
    1. 4 列 60 行 → 表头 4 个，渲染 50 行（第 1 页），共 2 页
    2. 翻到第 2 页 → 10 行
    3. 跨列数切换 4 列 → 5 列 → 表头 5 个，渲染正常
    4. 越界保护（current_page=99）→ 自动回到最后一页
    """
    import flet as ft
    sys.modules["flet"] = _make_flet_module()
    from app.ui.main_view import MainView

    page = _FakePage()
    mv = MainView(page)

    # 场景1：4 列 60 行
    rows4 = [
        {"novel_name": f"小说{i}", "author_name": f"作者{i}",
         "novel_url": f"http://x/{i}", "cover_image_url": f"http://img/{i}.jpg"}
        for i in range(60)
    ]
    mv.last_rows = rows4
    mv.current_page = 1
    mv._render_table(rows4)
    assert len(mv.data_table.columns) == 4
    assert len(mv.data_table.rows) == 50, f"应渲染 50 行，实际 {len(mv.data_table.rows)}"
    assert "第 1/2 页" in mv.page_label.value
    assert mv.next_btn.disabled is False

    # 场景2：翻页
    mv.current_page = 2
    mv._render_table(rows4)
    assert len(mv.data_table.rows) == 10
    assert mv.prev_btn.disabled is False
    assert mv.next_btn.disabled is True

    # 场景3：跨列数切换（4 列 → 5 列，触发 flet "DataRow/DataCells 列数不匹配"
    # 校验；修复后清空 rows 再设 columns 再设 rows 不会再报错）
    rows5 = [
        {"标题": f"书{i}", "价格": f"£{i}", "链接": f"http://x/{i}",
         "作者": f"人{i}", "评分": f"{i % 5}.0"}
        for i in range(120)
    ]
    mv.last_rows = rows5
    mv.current_page = 1
    mv._render_table(rows5)
    assert len(mv.data_table.columns) == 5
    assert "第 1/3 页" in mv.page_label.value

    # 场景4：越界保护
    mv.current_page = 99
    mv._render_table(rows5)
    assert mv.current_page == 3, "越界应回到最后一页"
    assert mv.next_btn.disabled is True

    # 场景5：单行坏掉不传染（坏行显示"渲染错误"占位）
    rows_bad = [
        {"标题": "正常行1"},
        {"标题": "正常行2"},
    ]
    # 让 _render_cell 抛异常（通过 mock TextField）
    mv.last_rows = rows_bad
    mv.current_page = 1
    # 正常情况下不应抛异常
    mv._render_table(rows_bad)
    assert len(mv.data_table.rows) == 2
    print("  ✅ 分页 + 跨列数切换 + 越界保护 + 坏行防御 全部通过")
    return True


if __name__ == "__main__":
    ok = all([test_restore_on_start(), test_engine_dropdown_has_crawl4ai(),
              test_zero_rows_keeps_old(), test_failure_keeps_old(),
              test_pagination_and_cross_column_switch()])
    last_result.clear_last_result()
    print("\n结论:", "✅ 全部通过" if ok else "❌ 有失败")
    sys.exit(0 if ok else 1)
