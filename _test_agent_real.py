# -*- coding: utf-8 -*-
"""真实 DeepSeek 端到端：agent 引擎（官方栈）实战。

AI 需要：看【源代码结构】→ 从真实 DOM 找字段 → extract。
验证新版引擎在真实 LLM 下的表现。
"""
import sys
import json
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")

def get_key():
    cfg = json.load(open("config.json", encoding="utf-8"))
    return cfg.get("api_key", "")


def run_test():
    from app.agent_engine import BrowserAgentEngine
    engine = BrowserAgentEngine(headless=True)
    result = {}
    def worker():
        try:
            r = engine.run(
                "提取这个页面上所有书籍的标题、价格和链接",
                url="https://books.toscrape.com/",
                api_key=get_key(),
                progress=lambda m: print(f"  [progress] {m}"),
                max_steps=6,
            )
            result["ok"] = {"rows": r.rows, "attempts": r.attempts}
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=600)
    return result


if __name__ == "__main__":
    print("=== 真实 DeepSeek 端到端（agent 引擎官方栈）===")
    res = run_test()
    if "ok" in res:
        rows = res["ok"]["rows"]
        attempts = res["ok"]["attempts"]
        print(f"\n结果: {len(rows)} 行，AI 决策 {attempts} 步")
        for r in rows[:3]:
            print(f"  {r}")
        assert len(rows) >= 10, "应提取到至少 10 行"
        assert all(r.get("价格") for r in rows[:5]), "价格字段应有值"
        print("\n✅ 真实 AI 端到端通过")
        sys.exit(0)
    else:
        print("\n❌", res.get("err", "")[:2000])
        sys.exit(1)
