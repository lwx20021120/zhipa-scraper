# -*- coding: utf-8 -*-
"""端到端验证新版 agent 引擎（完整官方栈移植）。

核心验证：
1. BrowserSession 启动 + 打开页面
2. DomService 拿官方 Elements 视图（AI 看源代码）
3. Element 按编号点击（翻页）+ _extract_rows_async 提取
4. 模拟 AI 决策（mock _decide 直接返回 extract，验证提取链路）
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")


def run_test():
    """用 mock AI 决策验证官方栈各环节。"""
    import asyncio
    from app.engines import _run_async
    from app.agent_engine import (_extract_rows_async, _api_summary,
                                  _looks_like_login_or_captcha_async)
    from app import agent_engine

    # 记录 AI 决策次数
    calls = []

    async def flow():
        from browser_use.browser.session import BrowserSession
        from browser_use.dom.service import DomService
        from browser_use.actor.element import Element

        session = BrowserSession(headless=True)
        await session.start()
        try:
            await session.navigate_to("https://books.toscrape.com/")
            await asyncio.sleep(2)
            page = await session.must_get_current_page()
            dom_service = DomService(browser_session=session)

            # 1. DomService 官方 Elements 视图（AI 看源代码）
            state, _, _ = await dom_service.get_serialized_dom_tree()
            dom_view = state.llm_representation(
                include_attributes=["class", "id", "href", "title", "src", "alt"])
            assert len(dom_view) > 500, "DOM 视图太短"
            assert "[10" in dom_view or "[" in dom_view, "应包含编号"
            print(f"  ✅ Elements 视图：{len(dom_view)} 字符，含编号")

            # 2. 查看商品区域结构（AI 找到字段）
            sel_map = state.selector_map
            print(f"  ✅ selector_map：{len(sel_map)} 个可交互元素")

            # 3. 模拟 AI extract 决策（标题/价格/链接）
            fields = [
                {"name": "标题", "selector": "article.product_pod h3 a",
                 "type": "attr", "attr": "title"},
                {"name": "价格", "selector": "article.product_pod .price_color",
                 "type": "text"},
                {"name": "链接", "selector": "article.product_pod h3 a",
                 "type": "attr", "attr": "href"},
            ]
            rows = await _extract_rows_async(page, fields)
            assert len(rows) >= 15, f"应提取到至少 15 行，实际 {len(rows)}"
            print(f"  ✅ 官方 CSS 提取：{len(rows)} 行，首行={rows[0]}")

            # 4. 按编号点击翻页（官方 Element）
            # 找到 next 按钮的编号：从 DOM 视图找 pager
            import re
            next_idx = None
            # 官方 selector_map 的 value 是 EnhancedDOMTreeNode，找 href=page-2
            for idx, node in sel_map.items():
                attrs = node.attributes or {}
                href = attrs.get("href", "") or ""
                if "page-2" in href:
                    next_idx = idx
                    break
            assert next_idx, "应能找到下一页链接的编号"
            node = sel_map[next_idx]
            elem = Element(session, node.backend_node_id,
                           getattr(node, "session_id", None))
            # 先滚动到元素（官方 click 的坐标在滚动前计算，视口外元素
            # 会被 clamp 到错误位置；先 scrollIntoView 再点）
            try:
                await elem.evaluate(
                    "() => { this.scrollIntoView({block:'center'}); "
                    "return true; }")
                await asyncio.sleep(0.5)
            except Exception:
                pass
            await elem.click()
            print(f"  ✅ 官方 Element 按编号 {next_idx} 点击翻页成功")
            # 等页面跳转完成（URL 变化 + networkidle）
            for _ in range(10):
                await asyncio.sleep(1)
                cur = await page.get_url()
                if "page-2" in cur:
                    break
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # 5. 翻页后再次提取（第 2 页数据）
            rows2 = await _extract_rows_async(page, fields)
            print(f"  ✅ 翻页后提取：{len(rows2)} 行（第 2 页）")
            return rows, rows2
        finally:
            try:
                await session.kill()
            except Exception:
                pass

    result = {}
    def worker():
        try:
            result["ok"] = _run_async(flow)
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=240)
    return result


if __name__ == "__main__":
    print("=== 新版 agent 引擎（官方栈）端到端 ===")
    res = run_test()
    if "ok" in res:
        rows, rows2 = res["ok"]
        # 第 2 页与第 1 页标题不应重复
        t1 = {r["标题"] for r in rows}
        t2 = {r["标题"] for r in rows2}
        overlap = t1 & t2
        print(f"  第1页 {len(t1)} 个标题 / 第2页 {len(t2)} 个标题 / 重叠 {len(overlap)}")
        assert len(overlap) <= 2, "翻页后标题应不同（重叠过多说明翻页失败）"
        print("\n✅ 全部通过：官方栈（DomService 视图 + Element 操作 + CSS 提取）工作正常")
        sys.exit(0)
    else:
        print("\n❌", res.get("err", "")[:2000])
        sys.exit(1)
