# -*- coding: utf-8 -*-
"""验证直接用 browser-use 官方 DomService 获取「开发者工具 DOM 视图」。

这是最彻底的移植——不重写，直接调用官方完整实现：
- BrowserSession 启动（复用我们的弹窗/登录态逻辑）
- DomService.get_serialized_dom_tree() 拿官方序列化 DOM（Elements 视图）
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")
from app.engines import _run_async


def run_test():
    from browser_use.browser.session import BrowserSession
    from browser_use.dom.service import DomService

    async def flow():
        session = BrowserSession(headless=True)
        await session.start()
        try:
            await session.navigate_to("https://books.toscrape.com/")
            import asyncio
            await asyncio.sleep(2)
            dom_service = DomService(browser_session=session)
            state, tree_root, timing = await dom_service.get_serialized_dom_tree()
            return state, tree_root
        finally:
            try:
                await session.kill()
            except Exception:
                pass

    result = {}
    def worker():
        try:
            state, tree_root = _run_async(flow)
            result["ok"] = (state, tree_root)
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=180)
    return result


if __name__ == "__main__":
    print("=== 官方 DomService 直测 ===")
    res = run_test()
    if "ok" in res:
        state, tree_root = res["ok"]
        # SerializedDOMState 有 _root 和 selector_map
        sel_map = getattr(state, "selector_map", None)
        print("selector_map 大小:", len(sel_map) if sel_map else "无")
        # 官方 LLM 视图：llm_representation() 就是给 AI 看的完整 Elements 结构
        s = state.llm_representation()
        print("DOM 序列化长度:", len(s))
        print("--- 开头 1000 字符 ---")
        print(s[:1000])
        print("✅ 官方 DomService 可用")
        sys.exit(0)
    else:
        print("❌", res.get("err", "")[:1500])
        sys.exit(1)
