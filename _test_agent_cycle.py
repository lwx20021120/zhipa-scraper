# -*- coding: utf-8 -*-
"""端到端验证新版 agent 引擎的完整 AI 决策循环（mock LLM 决策）。

模拟 AI 行为（对应真实 DeepSeek 决策）：
1. 步骤1：inspect 查看商品列表区域源码（AI 查源代码）
2. 步骤2：extract 用从源码中得到的真实 selector 提取
验证：引擎能拿到 DOM 视图、执行 inspect、按真实结构提取数据。
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")

# mock _decide：按步骤返回预设决策
PRESET_DECISIONS = [
    # 步骤1：AI 先 inspect 商品区域（看源代码）
    {"action": "inspect", "selector": ".product_pod"},
    # 步骤2：AI 从源码结构推断出 selector，extract
    {"action": "extract", "fields": [
        {"name": "标题", "selector": "article.product_pod h3 a",
         "type": "attr", "attr": "title"},
        {"name": "价格", "selector": "article.product_pod .price_color",
         "type": "text"},
        {"name": "链接", "selector": "article.product_pod h3 a",
         "type": "attr", "attr": "href"},
    ]},
]


def run_test():
    import app.agent_engine as ae

    # 记录传给 AI 的状态（验证 DOM 视图在里面）
    seen_states = []

    orig_decide = ae._decide

    def fake_decide(task, state, step, api_key, history=""):
        seen_states.append(state)
        idx = min(step - 1, len(PRESET_DECISIONS) - 1)
        return PRESET_DECISIONS[idx]

    ae._decide = fake_decide
    try:
        from app.agent_engine import BrowserAgentEngine
        engine = BrowserAgentEngine(headless=True)
        result = {}
        def worker():
            try:
                r = engine.run(
                    "提取页面上所有书籍的标题、价格和链接",
                    url="https://books.toscrape.com/",
                    api_key="sk-test-fake",
                    progress=lambda m: print(f"  [progress] {m}"),
                    max_steps=4,
                )
                result["ok"] = {"rows": r.rows, "attempts": r.attempts}
            except Exception as e:
                import traceback
                result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=300)
        return result, seen_states
    finally:
        ae._decide = orig_decide


if __name__ == "__main__":
    print("=== 新版 agent 引擎 AI 决策循环（mock）===")
    res, states = run_test()
    if "ok" in res:
        rows = res["ok"]["rows"]
        attempts = res["ok"]["attempts"]
        print(f"  结果: {len(rows)} 行，attempts={attempts}")
        if rows:
            print(f"  首行: {rows[0]}")
        # 验证状态里有 DOM 视图
        has_dom = any("源代码结构" in s for s in states)
        has_api = any("api" in s for s in states)
        print(f"  状态含【源代码结构】: {has_dom} / 含【api 摘要】: {has_api}")
        assert len(rows) >= 15, "应提取到至少 15 行"
        assert has_dom, "AI 状态应包含 DOM 结构视图"
        assert attempts >= 2, "应经过 inspect→extract 两步"
        assert rows[0].get("价格"), ("价格字段应有值（说明 AI 的 extract 生效，"
                                     "而非通用兜底）")
        print("\n✅ 通过：AI 决策循环（inspect→extract）+ DOM 视图 + 官方提取全链路正常")
        sys.exit(0)
    else:
        print("\n❌", res.get("err", "")[:2000])
        sys.exit(1)
