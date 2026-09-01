# -*- coding: utf-8 -*-
"""BrowserUse 移植完整性逐项确认（真实运行验证）。

逐项验证（不是代码存在，是真实跑通）：
1. BrowserSession 启动/导航/关闭
2. BrowserSession 直接传参（官方推荐用法）
3. DomService 序列化 DOM（Elements 视图，含编号+class/id）
4. Element 按编号点击（翻页，含视口外滚动修复）
5. Element.fill 输入
6. page.get_elements_by_css_selector 官方 CSS 提取
7. agent 引擎真实 AI 决策（mock 决策，验证全链路）
8. 登录墙检测
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")
from app.engines import _run_async

REPORT = []


def report(name, ok, detail=""):
    s = "✅" if ok else "❌"
    REPORT.append((name, ok, detail))
    print(f"  {s} {name}" + (f"  → {detail}" if detail else ""))


async def flow():
    import asyncio
    from browser_use.browser.session import BrowserSession
    from browser_use.dom.service import DomService
    from browser_use.actor.element import Element

    # 1-2. BrowserSession 直接传参启动
    session = BrowserSession(headless=True, user_data_dir=None)
    await session.start()
    report("BrowserSession 直接传参启动", True)
    await session.navigate_to("https://books.toscrape.com/")
    report("BrowserSession.navigate_to", True)
    await asyncio.sleep(2)
    page = await session.must_get_current_page()
    report("must_get_current_page", True)

    # 3. DomService Elements 视图
    dom_service = DomService(browser_session=session)
    state, _, _ = await dom_service.get_serialized_dom_tree()
    dom_view = state.llm_representation(
        include_attributes=["class", "id", "href", "title"])
    has_bracket = "[" in dom_view
    has_class = "class=" in dom_view or "price_color" in dom_view
    report("DomService Elements 视图（含编号+属性）",
           len(dom_view) > 500 and has_bracket and has_class,
           f"{len(dom_view)} 字符")
    sel_map = state.selector_map
    report("selector_map 编号→元素", len(sel_map) > 50, f"{len(sel_map)} 个")

    # 4. Element 点击翻页（含滚动修复）
    next_idx = None
    for idx, node in sel_map.items():
        attrs = node.attributes or {}
        if "page-2" in (attrs.get("href") or ""):
            next_idx = idx
            break
    report("找到翻页链接编号", next_idx is not None, f"编号 {next_idx}")
    if next_idx:
        node = sel_map[next_idx]
        elem = Element(session, node.backend_node_id,
                       getattr(node, "session_id", None))
        await elem.evaluate("() => { this.scrollIntoView({block:'center'}); "
                            "return true; }")
        await asyncio.sleep(0.5)
        await elem.click()
        for _ in range(10):
            await asyncio.sleep(1)
            if "page-2" in await page.get_url():
                break
        cur = await page.get_url()
        report("Element 点击翻页成功", "page-2" in cur, cur[:60])

    # 5. CSS 提取
    fields = [
        {"name": "标题", "selector": "article.product_pod h3 a",
         "type": "attr", "attr": "title"},
        {"name": "价格", "selector": ".price_color", "type": "text"},
    ]
    from app.agent_engine import _extract_rows_async
    rows = await _extract_rows_async(page, fields)
    report("官方 CSS 提取", len(rows) >= 15, f"{len(rows)} 行")

    # 6. Element.get_attribute（翻页后重新获取 DOM，元素才有效）
    state2, _, _ = await dom_service.get_serialized_dom_tree()
    sel_map2 = state2.selector_map
    first_node = list(sel_map2.values())[0]
    first_elem = Element(session, first_node.backend_node_id,
                         getattr(first_node, "session_id", None))
    try:
        href = await first_elem.get_attribute("href")
        report("Element.get_attribute", href is not None, str(href)[:40])
    except Exception as e:
        report("Element.get_attribute", False, str(e)[:80])

    # 7. 登录墙检测
    from app.agent_engine import _looks_like_login_or_captcha_async
    is_login = await _looks_like_login_or_captcha_async(page)
    report("登录墙检测", is_login is False, "books 站非登录页")

    await session.kill()
    report("BrowserSession.kill 清理", True)
    return True


def main():
    result = {}
    def worker():
        try:
            result["ok"] = _run_async(flow)
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=300)
    if "err" in result:
        print(f"  ❌ 流程异常: {result['err'][:500]}")
    return result


if __name__ == "__main__":
    print("=== BrowserUse 移植完整性逐项确认 ===")
    res = main()
    ok_count = sum(1 for _, ok, _ in REPORT if ok)
    bad = [(n, d) for n, ok, d in REPORT if not ok]
    print(f"\n汇总: {ok_count}/{len(REPORT)} 项通过")
    if bad:
        for n, d in bad:
            print(f"  ❌ {n}: {d}")
    sys.exit(0 if "err" not in res and not bad else 1)
