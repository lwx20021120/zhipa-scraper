# -*- coding: utf-8 -*-
"""融合引擎泛化方案 A 测试：LLM 场景分析器。

场景：
1. 无 API key → 降级到关键词（仍是原体验）
2. 有 API key 但余额不足 → LLM 失败降级到关键词（用户无感）
3. mock LLM 返回结构化 JSON → LLM 场景分析生效（泛化能力）
4. mock LLM 返回非 JSON → 降级
5. LLM 输出的 fields 传入 PlanBuilder 能正确解析
"""
import sys
from unittest.mock import patch

sys.path.insert(0, r"D:\workbuudy\Scrapling")


def test_no_api_key():
    """无 API key：直接走关键词分析。"""
    from app.unified_engine import LLMScenarioAnalyzer, ScenarioProfile
    a = LLMScenarioAnalyzer(api_key="")
    p = a.analyze("提取书籍标题价格",
                  url="https://books.toscrape.com/",
                  progress=lambda m: None)
    assert isinstance(p, ScenarioProfile), "必须返回 ScenarioProfile"
    assert p.scenario in ("static_page", "dynamic_page")
    print(f"  ✅ 无API: {p.scenario}（置信度 {p.confidence}）")
    return True


def test_llm_402_falls_back():
    """DeepSeek 余额不足：LLM 失败应降级到关键词，不阻塞。"""
    from app.unified_engine import LLMScenarioAnalyzer

    def fake_call_llm(messages, api_key, retries=3):
        raise RuntimeError("Insufficient Balance (402)")

    # unified_engine 模块顶部 import 了 _call_llm，patch 模块级的引用
    with patch("app.unified_engine._call_llm", fake_call_llm):
        a = LLMScenarioAnalyzer(api_key="sk-fake-with-402")
        p = a.analyze("提取豆瓣电影Top250的标题评分链接",
                      url="https://movie.douban.com/top250",
                      progress=lambda m: None)
    assert p.scenario, "降级后仍应有场景画像"
    print(f"  ✅ LLM失败降级: {p.scenario}（原因: {p.reasons[0] if p.reasons else ''}）")
    return True


def test_llm_success():
    """mock LLM 返回正确 JSON → 场景分析生效，泛化能力。"""
    from app.unified_engine import LLMScenarioAnalyzer

    llm_response = """{"scenario": "dynamic_page",
"primary_engine": "scrapling",
"fallback_engines": ["direct", "agent"],
"mode": "race",
"needs_login": false,
"needs_pagination": true,
"needs_deep_crawl": false,
"fields": [
  {"name": "标题", "type": "text"},
  {"name": "评分", "type": "text"},
  {"name": "链接", "type": "attr", "attribute": "href"}
],
"reasoning": "用户要 Top250 列表含分页，普通分页列表",
"confidence": 0.88}"""

    def fake_call_llm(messages, api_key, retries=3):
        return llm_response

    with patch("app.unified_engine._call_llm", fake_call_llm):
        a = LLMScenarioAnalyzer(api_key="sk-fake-but-works")
        p = a.analyze("提取豆瓣电影Top250的标题评分链接",
                      url="https://movie.douban.com/top250",
                      progress=lambda m: None)
    # 修复后：needs_pagination=true 会被升级到 deep_crawl（不再被 race 抢跑覆盖）
    assert p.scenario == "deep_crawl", (
        f"needs_pagination=true 应升级到 deep_crawl，实际 {p.scenario}")
    assert p.is_deep, "needs_pagination 触发后应 is_deep=True"
    assert len(p.fields_hint) == 3, f"应有 3 个字段，实际 {len(p.fields_hint)}"
    assert p.confidence == 0.88
    # 验证 LLM 解析的字段结构
    assert p.fields_hint[0]["name"] == "标题"
    assert p.fields_hint[1]["name"] == "评分"
    assert p.fields_hint[2]["type"] == "attr"
    print(f"  ✅ LLM成功: {p.scenario}（needs_pagination 升级到 deep_crawl）, "
          f"字段={[f['name'] for f in p.fields_hint]}, 置信度={p.confidence}")
    return True


def test_llm_invalid_json_falls_back():
    """LLM 返回非 JSON（如被截断） → 降级到关键词。"""
    from app.unified_engine import LLMScenarioAnalyzer

    def fake_call_llm(messages, api_key, retries=3):
        return "抱歉，AI 思考中断...（非 JSON）"

    with patch("app.unified_engine._call_llm", fake_call_llm):
        a = LLMScenarioAnalyzer(api_key="sk-fake")
        p = a.analyze("爬取所有分类页面",
                      url="https://books.toscrape.com/",
                      progress=lambda m: None)
    assert p.scenario, "降级后仍有场景画像"
    print(f"  ✅ LLM非JSON降级: {p.scenario}")
    return True


def test_llm_response_to_eng_runs():
    """端到端——LLM 分析 + 实际抓取 books 站。"""
    from app.unified_engine import LLMScenarioAnalyzer, UnifiedEngine

    llm_response = """{"scenario": "static_page",
"primary_engine": "scrapling",
"fallback_engines": ["crawl4ai"],
"mode": "race",
"needs_login": false,
"needs_pagination": false,
"needs_deep_crawl": false,
"fields": [{"name": "标题", "type": "attr", "attribute": "title"},
           {"name": "价格", "type": "text"}],
"reasoning": "简单列表",
"confidence": 0.9}"""

    def fake_call_llm(messages, api_key, retries=3):
        return llm_response

    with patch("app.unified_engine._call_llm", fake_call_llm):
        # 真实跑 UnifiedEngine 会触发 Crawl4AI 网络抓取（依赖真实 API + 网络）
        # 改为 mock 所有引擎的 run 方法，只测 LLM 决策→Plan→Arbiter 完整链路
        from app.engines import EngineResult
        fake_rows = [
            {"标题": f"书{i}", "价格": f"£{i}.0"} for i in range(20)
        ]
        fake_result = EngineResult(
            rows=fake_rows, status=0, used_fetcher="scrapling", engine="scrapling",
            attempts=1, config={"fields": [{"name": "标题"}, {"name": "价格"}]})
        with patch("app.engines.ScraplingEngine.run",
                   return_value=fake_result), \
             patch("app.engines.Crawl4AIEngine.run",
                   return_value=fake_result), \
             patch("app.engines.DirectExtractEngine.run",
                   return_value=fake_result):
            eng = UnifiedEngine(headless=True)
            r = eng.run("提取书籍标题价格",
                        url="https://books.toscrape.com/",
                        api_key="sk-fake", progress=lambda m: None)
        assert len(r.rows) >= 15, f"应至少 15 行，实际 {len(r.rows)}"
        print(f"  ✅ LLM决策+Arbiter完整链路: {len(r.rows)} 行（mock引擎避免依赖真实网络）")
        return True


if __name__ == "__main__":
    print("=== 融合引擎泛化方案 A 测试 ===")
    ok = all([test_no_api_key(),
              test_llm_402_falls_back(),
              test_llm_success(),
              test_llm_invalid_json_falls_back(),
              test_llm_response_to_eng_runs()])
    print("\n结论:", "✅ 全部通过" if ok else "❌ 有失败")
    sys.exit(0 if ok else 1)