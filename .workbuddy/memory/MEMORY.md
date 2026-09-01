# 智爬项目长期笔记（Scrapling）

## 核心工作规范（用户强调，必须遵守）
1. **发现 bug 先翻原项目源码**：本项目功能大量移植自 `third_party/browser-use-main` 与 `third_party/crawl4ai-main`，遇到 bug/功能需求第一步到这两个仓库源码找官方解法（类定义、官方注释、examples/），源码里确实没有才自研，并注明
2. 官方 API 用法优先：BrowserSession 直接传参（不包 BrowserProfile）；嵌入 flet 时 Agent(enable_signal_handler=False)
3. 每个修复/新功能写独立 `_test_*.py` 测试脚本验证后再交付

## 技术要点
- Python 3.13（D:\python\python.exe）+ flet 0.86.5 + scrapling 0.4.15 + browser-use 0.13.8 + nest_asyncio
- `_run_async` 处理 flet 嵌套 loop：无 loop → asyncio.run；有 loop → nest_asyncio + loop.create_task（勿用 ensure_future(loop=)，3.12+ 弃用）
- 上次结果持久化：app/last_result.py（.last_result.json），web 刷新不丢数据
- 运行：`D:\python\python.exe -m app.main`（桌面）/ `run_web.py`（web，8550 端口）/ `启动智爬.bat`
