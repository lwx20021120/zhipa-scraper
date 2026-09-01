# -*- coding: utf-8 -*-
"""验证 BrowserUseEngine 弹窗改造核心：BrowserSession 共享 + navigate + 生命周期。

用真实的 BrowserSession 跑一遍（不调用 LLM，只验证浏览器会话层），
看 session.start / navigate_to / must_get_current_page / kill 是否正常。
"""
import sys
import threading
import asyncio

sys.path.insert(0, r"D:\workbuudy\Scrapling")
from app.engines import _run_async, _is_login_wall_text


def run_browser_session_test():
    from browser_use.browser.profile import BrowserProfile
    from browser_use.browser.session import BrowserSession

    async def _agent_flow():
        profile = BrowserProfile(headless=True)
        session = BrowserSession(browser_profile=profile)
        await session.start()
        try:
            await session.navigate_to("https://example.com")
            await asyncio.sleep(1)
            page = await session.must_get_current_page()
            body_text = await page.evaluate(
                "() => (document.body.innerText || '').slice(0, 500)")
            print("  页面文本:", body_text[:120].replace("\n", " "))
            return "browser-session-ok"
        finally:
            try:
                await session.kill()
            except Exception:
                pass

    result = {}
    def worker():
        try:
            r = _run_async(_agent_flow)
            result["ok"] = r
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=120)
    return result


def test_login_wall_detection():
    """验证登录墙检测函数。"""
    cases = [
        ("请扫码登录 请输入验证码", True),      # 登录墙
        ("商品列表 价格 立即购买", False),      # 正常页面
        ("sign in login captcha", True),
        ("欢迎来到首页", False),
    ]
    for text, expect in cases:
        got = _is_login_wall_text(text)
        print(f"  '{text[:20]}' → {got} (期望 {expect})")
    return all(_is_login_wall_text(t) == e for t, e in cases)


if __name__ == "__main__":
    print("=== 测试1: 登录墙检测 ===")
    ok1 = test_login_wall_detection()
    print("=== 测试2: BrowserSession 真实流程 ===")
    ok2 = run_browser_session_test()
    print("BrowserSession 结果:", ok2)
    print("\n结论:", "全部通过" if (ok1 and "ok" in ok2) else "存在问题")
    sys.exit(0 if (ok1 and "ok" in ok2) else 1)
