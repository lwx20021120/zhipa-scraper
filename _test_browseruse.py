# -*- coding: utf-8 -*-
"""临时测试：验证 BrowserUseEngine 改造后的共享会话 + Agent 复用链路。
跑完即删，不影响项目。"""
import sys
import asyncio

sys.path.insert(0, r"D:\workbuudy\Scrapling")

from app.engines import BrowserUseEngine
from app.config import load_config


def progress(msg):
    print("  [进度]", msg)


if __name__ == "__main__":
    key = load_config().get("api_key", "")
    eng = BrowserUseEngine(headless=True, user_data_dir="")
    print("engine.available =", eng.available())
    try:
        result = eng.run(
            "提取页面上的标题文字",
            url="https://example.com",
            api_key=key,
            max_retries=1,
            progress=progress,
        )
        print("RESULT rows =", result.rows[:3])
        print("ENGINE E2E OK")
    except Exception as e:
        print("ENGINE E2E FAILED:", type(e).__name__, str(e)[:300])
