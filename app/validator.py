# -*- coding: utf-8 -*-
"""数据抓取入口：调度多引擎完成 AI 抓取。

当前引擎：
    1. ScraplingEngine   —— selector 模式（默认）
    2. DirectExtractEngine —— AI 直提（兜底）
    3. BrowserUseEngine / BrowserAgentEngine —— 官方/自研 AI 浏览器
    4. Crawl4AIEngine —— 官方库（CSS/LLM 提取 + 深爬）
"""
from .engines import run_pipeline
from .scraper import ScrapeError


def run_ai_scrape(user_input: str, url: str = "", api_key: str = "",
                  max_retries: int = 3, proxy: str = "",
                  progress: callable = None, engine: str = "auto",
                  headless: bool = True, user_data_dir: str = "",
                  **kwargs) -> tuple:
    """完整 AI 抓取（多引擎调度）。

    参数：
        progress: 进度回调 progress(msg: str)
        engine: 指定引擎（auto/scrapling/direct/agent/browser-use/crawl4ai）
        headless: agent 引擎是否无头（False 弹浏览器窗口可手动登录）
        user_data_dir: agent 浏览器数据目录（持久化登录态）
        **kwargs: 引擎专属参数（如 crawl4ai 的 deep_max_depth/max_pages/
                  cache_mode/use_llm_extraction）
    返回 (rows, config, attempts, used_fetcher)。
    全程失败抛 ScrapeError。
    """
    result = run_pipeline(user_input, url, api_key, max_retries, proxy,
                          progress, engine=engine, headless=headless,
                          user_data_dir=user_data_dir, **kwargs)
    return result.rows, result.config, result.attempts, result.used_fetcher
