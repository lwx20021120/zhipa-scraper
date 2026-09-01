# -*- coding: utf-8 -*-
"""融合引擎端到端测试：真实抓取验证路由/竞争/仲裁。

场景：
1. 静态页（books）→ 自动 static 场景 → race 竞争
2. 整站深爬 → deep 场景 → Crawl4AI BFS
3. 仲裁器：多引擎结果选优 + 字段融合
4. 质量评分器
5. 与 run_pipeline 的 auto 集成（默认走融合引擎）
"""
import sys
import json
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")

API_KEY = json.load(open("config.json", encoding="utf-8")).get("api_key", "")


def test_scenario_routing():
    """场景路由（不抓取，纯逻辑）。"""
    from app.unified_engine import ScenarioAnalyzer, PlanBuilder
    analyzer = ScenarioAnalyzer()
    builder = PlanBuilder()
    cases = [
        ("提取页面上所有书籍的标题、价格和链接",
         "https://books.toscrape.com/", "static_page"),
        ("爬取这个网站的所有分类页面，提取书籍标题",
         "https://books.toscrape.com/", "deep_crawl"),
        ("需要登录的页面，提取数据",
         "https://example.com/login", "login_page"),
        ("提取接口返回的json数据",
         "https://api.example.com/v1/products", "json_api"),
        ("爬取这个动态加载的页面",
         "https://example.com/spa", "dynamic_page"),
    ]
    ok = True
    for ui, url, expect in cases:
        prof = analyzer.analyze(ui, url, progress=lambda m: None)
        status = "✅" if prof.scenario == expect else "❌"
        if prof.scenario != expect:
            ok = False
        plan = builder.build(prof)
        nodes = " → ".join(f"{n.mode}({'+'.join(n.engine_names)})"
                           for n in plan.nodes)
        print(f"  {status} {expect:12s} → {nodes}")
    return ok


def test_quality_scorer():
    """质量评分器。"""
    from app.unified_engine import QualityScore
    good = [{"标题": f"书{i}", "价格": f"£{i}.00"} for i in range(20)]
    bad = [{"标题": "", "价格": ""} for _ in range(20)]
    q1 = QualityScore().compute(good)
    q2 = QualityScore().compute(bad)
    print(f"  好数据: {q1.total}分（行{q1.rows_count} 完整率"
          f"{round(q1.field_completeness*100)}%）")
    print(f"  坏数据: {q2.total}分（行{q2.rows_count} 完整率"
          f"{round(q2.field_completeness*100)}%）")
    assert q1.total > q2.total, "好数据分数应更高"
    return True


def test_arbiter_fusion():
    """仲裁器：多引擎结果选优 + 字段融合。"""
    from app.unified_engine import Arbiter
    arb = Arbiter()
    results = {
        "engine_a": type("R", (), {
            "rows": [{"标题": f"书{i}", "价格": f"£{i}"} for i in range(15)],
            "config": {"fields": []}})(),
        "engine_b": type("R", (), {
            "rows": [{"标题": f"书{i}", "链接": f"http://x/{i}"}
                     for i in range(15)],
            "config": {"fields": []}})(),
    }
    best_name, best, scores = arb.pick_best(results)
    print(f"  选优: {best_name}（分{scores}）")
    fused = arb.fuse_fields(results)
    print(f"  融合: {len(fused)} 行，字段={list(fused[0].keys())}")
    assert "价格" in fused[0] and "链接" in fused[0], "融合应包含两引擎字段"
    return True


def test_unified_static():
    """融合引擎静态页端到端。"""
    from app.unified_engine import UnifiedEngine
    eng = UnifiedEngine(headless=True)
    result = {}
    def worker():
        try:
            r = eng.run(
                "提取页面上所有书籍的标题、价格和链接",
                url="https://books.toscrape.com/",
                api_key=API_KEY,
                progress=lambda m: print(f"  [progress] {m}"),
            )
            result["ok"] = r
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=300)
    if "ok" in result:
        r = result["ok"]
        print(f"  ✅ 融合静态页: {len(r.rows)} 行，引擎={r.engine}")
        return len(r.rows) >= 15
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


def test_unified_deep():
    """融合引擎整站深爬。"""
    from app.unified_engine import UnifiedEngine
    eng = UnifiedEngine(headless=True)
    result = {}
    def worker():
        try:
            r = eng.run(
                "爬取这个网站的所有分类页面，提取书籍标题和价格",
                url="https://books.toscrape.com/",
                api_key=API_KEY,
                progress=lambda m: print(f"  [progress] {m}"),
            )
            result["ok"] = r
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=400)
    if "ok" in result:
        r = result["ok"]
        print(f"  ✅ 融合深爬: {len(r.rows)} 行，引擎={r.engine}")
        return len(r.rows) > 100
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


def test_pipeline_integration():
    """run_pipeline auto → 融合引擎。"""
    from app.engines import run_pipeline
    result = {}
    def worker():
        try:
            r = run_pipeline(
                "提取页面上所有书籍的标题、价格和链接",
                url="https://books.toscrape.com/",
                api_key=API_KEY,
                progress=lambda m: print(f"  [progress] {m}"),
            )
            result["ok"] = r
        except Exception as e:
            result["err"] = str(e)
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=300)
    if "ok" in result:
        r = result["ok"]
        print(f"  ✅ run_pipeline(auto) → 融合: {len(r.rows)} 行，"
              f"引擎={r.engine}")
        return len(r.rows) >= 15
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


if __name__ == "__main__":
    print("=== 融合引擎完整测试 ===")
    ok1 = test_scenario_routing()
    ok2 = test_quality_scorer()
    ok3 = test_arbiter_fusion()
    ok4 = test_unified_static()
    ok5 = test_unified_deep()
    ok6 = test_pipeline_integration()
    ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6
    print("\n结论:", "✅ 全部通过" if ok else "❌ 有失败")
    sys.exit(0 if ok else 1)
