# -*- coding: utf-8 -*-
"""端到端验证 BrowserUseEngine：mock LLM 避免消耗 API。

重点验证：
1. BrowserSession 共享 + 30 秒登录窗口逻辑（弹窗改造）
2. agent.run 在 _run_async 下的异步 loop 行为（嵌套 loop 排查）
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")

# ---- mock LLM：让 browser-use 的 Agent 快速返回固定 JSON ----
FAKE_OUTPUT = ('[{"标题":"Test Item 1","价格":"$9.99","链接":"https://example.com/1"},'
               '{"标题":"Test Item 2","价格":"$19.99","链接":"https://example.com/2"}]')


class FakeChatOpenAI:
    """替换 langchain_openai.ChatOpenAI，bind_tools 等方法返回自身。"""
    def __init__(self, *args, **kwargs):
        pass

    def bind_tools(self, *a, **kw):
        return self

    def with_structured_output(self, *a, **kw):
        return self

    async def ainvoke(self, *a, **kw):
        # browser-use token 统计服务会访问 llm.ainvoke，返回一个空响应即可
        class _R:
            def content(self):
                return ""
        return _R()

    # langchain 兼容
    provider = "deepseek"
    model = "deepseek-chat"


def patch_llm():
    import browser_use.agent.service as svc
    import browser_use.agent.views as views
    import langchain_openai
    # 替换所有引用点
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    for mod in (svc, views):
        if hasattr(mod, "ChatOpenAI"):
            setattr(mod, "ChatOpenAI", FakeChatOpenAI)

    # mock agent.run 返回可解析的 final_result
    from browser_use.agent.service import Agent
    async def fake_run(self, max_steps=500, on_step_start=None, on_step_end=None):
        class FakeHistory:
            def final_result(self):
                return FAKE_OUTPUT
            def is_empty(self):
                return False
        return FakeHistory()
    Agent.run = fake_run


def run_full_test():
    patch_llm()
    from app.engines import BrowserUseEngine

    engine = BrowserUseEngine(headless=True)
    print("available():", engine.available())

    result = {}
    def worker():
        try:
            r = engine.run(
                "提取商品的标题、价格和链接",
                url="https://example.com",
                api_key="sk-test-fake",
                progress=lambda msg: print("  [progress]", msg),
            )
            result["ok"] = {
                "rows": r.rows,
                "engine": r.engine,
                "attempts": r.attempts,
            }
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=180)
    return result


if __name__ == "__main__":
    print("=== BrowserUseEngine 端到端（mock LLM）===")
    res = run_full_test()
    print("结果:", res)
    ok = "ok" in res
    print("\n结论:", "✅ 通过" if ok else "❌ 存在问题")
    sys.exit(0 if ok else 1)
