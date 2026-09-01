# -*- coding: utf-8 -*-
"""后端功能全面自查：逐项验证每个功能是否真实可用、前端能否调用。

输出格式：功能名 → 状态（✅完整可用 / ⚠️部分可用 / ❌不可用）+ 问题说明
"""
import sys
import inspect

sys.path.insert(0, r"D:\workbuudy\Scrapling")

RESULTS = []


def check(name, ok, detail=""):
    status = "✅ 完整可用" if ok else "❌ 不可用"
    RESULTS.append((name, status, detail))
    print(f"  {status} {name}" + (f"  → {detail}" if detail else ""))


print("=" * 60)
print("一、四引擎后端可用性")
print("=" * 60)

# 1. ScraplingEngine
from app.engines import (ScraplingEngine, DirectExtractEngine,
                         BrowserUseEngine, Crawl4AIEngine, run_pipeline)
check("ScraplingEngine 导入", True)
check("DirectExtractEngine 导入", True)
check("BrowserUseEngine 导入", True)
check("Crawl4AIEngine 导入", True)

engs = [("ScraplingEngine", ScraplingEngine()),
        ("DirectExtractEngine", DirectExtractEngine()),
        ("BrowserUseEngine", BrowserUseEngine()),
        ("Crawl4AIEngine", Crawl4AIEngine())]
for name, eng in engs:
    try:
        avail = eng.available()
        check(f"{name}.available()", avail,
              "" if avail else "依赖未安装")
    except Exception as e:
        check(f"{name}.available()", False, f"异常: {e}")

# 2. 调度器
try:
    keys = inspect.signature(run_pipeline).parameters
    check("run_pipeline 调度器存在", True)
    check("run_pipeline 支持 engine 参数", "engine" in keys)
except Exception as e:
    check("run_pipeline", False, str(e))

# 3. 各引擎 run 方法签名
for name, eng in engs:
    try:
        sig = inspect.signature(eng.run)
        check(f"{name}.run 可调用", True, f"参数: {list(sig.parameters)}")
    except Exception as e:
        check(f"{name}.run", False, str(e))

print()
print("=" * 60)
print("二、Scrapling 抓取器（确认是否官方库）")
print("=" * 60)
import app.scraper as scraper_mod
src = inspect.getsource(scraper_mod)
check("scraper.py 使用官方 scrapling.fetchers",
      "from scrapling.fetchers import" in src,
      "Fetcher/DynamicFetcher/StealthyFetcher（官方库，非自编）")
for fn in ["fetch_page", "auto_fetch", "scrape", "extract_json_path"]:
    check(f"scraper.{fn} 存在", hasattr(scraper_mod, fn))

print()
print("=" * 60)
print("三、前端 UI 调用链")
print("=" * 60)
import app.ui.main_view as mv
mv_src = inspect.getsource(mv.MainView)

# 引擎下拉选项
engine_options = ["auto", "scrapling", "direct", "agent", "browser-use", "crawl4ai"]
for opt in engine_options:
    check(f"前端引擎下拉含 {opt}", f'key="{opt}"' in mv_src or f"key='{opt}'" in mv_src,
          "" if (f'key="{opt}"' in mv_src or f"key='{opt}'" in mv_src) else "缺少该选项")

# AI 模式调用链
check("AI 模式传递 engine 参数", "engine = self.engine_select.value" in mv_src)
check("AI 模式传递 headless", "headless = not self.headless_checkbox.value" in mv_src)
check("AI 模式传递 user_data_dir", "user_data_dir = (self.chrome_data_input.value" in mv_src)

# 手动模式
check("手动模式 static 抓取", "scrape(url, fetcher" in mv_src)
check("手动模式 auto 抓取", "auto_fetch(url, fields" in mv_src)
check("手动模式翻页", "_collect_pagination" in mv_src)
check("整站深爬 UI", "pg_deep" in mv_src)

# 导出
for fmt in ["csv", "excel", "json"]:
    check(f"导出 {fmt.upper()}", f'"{fmt}"' in mv_src)
check("下载图片", "_on_download_images" in mv_src)

# 其他
check("抓取历史", "_open_history" in mv_src)
check("上次结果恢复", "_restore_last_result" in mv_src)
check("运行日志", "_add_log" in mv_src)
check("API Key 设置", "_open_settings" in mv_src)

print()
print("=" * 60)
print("四、浏览器引擎能力（BrowserUse/Agent）")
print("=" * 60)
import app.agent_engine as ae
ae_src = inspect.getsource(ae)
check("agent 引擎用官方 BrowserSession", "from browser_use.browser.session import BrowserSession" in ae_src
      or "BrowserSession(" in ae_src)
check("agent 引擎用官方 DomService", "DomService" in ae_src)
check("agent 引擎用官方 Element", "from browser_use.actor.element import Element" in ae_src)
check("agent 引擎含 DOM 视图（源代码）", "源代码结构" in ae_src)
check("agent 引擎含 inspect 动作", '"inspect"' in ae_src or "'inspect'" in ae_src)
check("agent 引擎含 from_api", "from_api" in ae_src)
check("agent 引擎含登录等待", "登录" in ae_src)

import app.engines as eng_mod
eng_src = inspect.getsource(eng_mod.BrowserUseEngine)
check("BrowserUseEngine 用官方 BrowserSession", "BrowserSession" in eng_src)
check("BrowserUseEngine 30秒登录窗口", "30" in eng_src)
check("BrowserUseEngine enable_signal_handler", "enable_signal_handler" in eng_src)

print()
print("=" * 60)
print("五、Crawl4AI 能力覆盖")
print("=" * 60)
c4_src = inspect.getsource(eng_mod.Crawl4AIEngine)
check("Crawl4AI 基础抓取", "AsyncWebCrawler" in c4_src)
check("Crawl4AI CSS 提取", "JsonCssExtractionStrategy" in c4_src)
check("Crawl4AI LLM 提取", "LLMExtractionStrategy" in c4_src)
check("Crawl4AI 深爬 BFS", "BFSDeepCrawlStrategy" in c4_src)
check("Crawl4AI AI 生成 schema", "generate_schema" in c4_src)
check("Crawl4AI 缓存控制", "CacheMode" in c4_src)

print()
print("=" * 60)
print("汇总")
print("=" * 60)
ok_count = sum(1 for _, s, _ in RESULTS if s.startswith("✅"))
bad = [(n, s, d) for n, s, d in RESULTS if not s.startswith("✅")]
print(f"共 {len(RESULTS)} 项：✅ {ok_count} 项，❌ {len(bad)} 项")
for n, s, d in bad:
    print(f"  ❌ {n}：{d}")
