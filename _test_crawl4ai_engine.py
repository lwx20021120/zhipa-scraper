# -*- coding: utf-8 -*-
"""Crawl4AI 引擎真实测试：官方库完整接入验证。

覆盖：
1. available() 检测
2. 单页 CSS 提取（JsonCssExtractionStrategy）
3. 深爬 BFS（整站）
4. 与现有引擎链集成
"""
import sys
import threading

sys.path.insert(0, r"D:\workbuudy\Scrapling")


def test_available():
    from app.engines import Crawl4AIEngine
    eng = Crawl4AIEngine()
    ok = eng.available()
    print(f"  Crawl4AIEngine.available(): {ok}")
    return ok


def test_single_page():
    """单页 CSS 提取（books.toscrape.com 首页 20 本书）。"""
    from app.engines import Crawl4AIEngine
    eng = Crawl4AIEngine()
    result = {}
    def worker():
        try:
            r = eng.run(
                "提取所有书籍的标题、价格和链接",
                url="https://books.toscrape.com/",
                api_key="",
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
        rows = result["ok"].rows
        print(f"  ✅ 单页提取: {len(rows)} 行")
        if rows:
            print(f"  首行: {rows[0]}")
        return len(rows) >= 5
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


def test_deep_crawl():
    """深爬 BFS（整站多页）。"""
    from app.engines import Crawl4AIEngine
    eng = Crawl4AIEngine()
    result = {}
    def worker():
        try:
            r = eng.run(
                "爬取这个网站的所有分类页面，提取书籍标题",
                url="https://books.toscrape.com/",
                api_key="",
                progress=lambda m: print(f"  [progress] {m}"),
                deep_max_depth=2,
                max_pages=5,
            )
            result["ok"] = r
        except Exception as e:
            import traceback
            result["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=300)
    if "ok" in result:
        rows = result["ok"].rows
        print(f"  ✅ 深爬提取: {len(rows)} 行")
        return len(rows) >= 5
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


def test_pipeline_integration():
    """调度器集成：指定 crawl4ai 引擎可运行。"""
    from app.engines import run_pipeline
    result = {}
    def worker():
        try:
            r = run_pipeline(
                "提取书籍标题、价格",
                url="https://books.toscrape.com/",
                engine="crawl4ai",
                progress=lambda m: print(f"  [progress] {m}"),
            )
            result["ok"] = r
        except Exception as e:
            result["err"] = str(e)
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=300)
    if "ok" in result:
        print(f"  ✅ 调度器集成: {len(result['ok'].rows)} 行，"
              f"引擎={result['ok'].engine}")
        return len(result["ok"].rows) >= 5
    else:
        print(f"  ❌ {result.get('err', '')[:500]}")
        return False


if __name__ == "__main__":
    print("=== Crawl4AI 引擎完整测试 ===")
    ok1 = test_available()
    ok2 = test_single_page()
    ok3 = test_deep_crawl()
    ok4 = test_pipeline_integration()
    print("\n结论:", "✅ 全部通过" if (ok1 and ok2 and ok3 and ok4) else "❌ 有失败")
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4) else 1)
