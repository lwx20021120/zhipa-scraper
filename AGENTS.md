# AGENTS.md — 智爬（AI 爬虫桌面应用）项目约束

## 核心工作规范

1. **发现 bug 先翻原项目源码，不自己拍脑袋修**
   - 本项目大量功能移植自 `third_party/browser-use-main` 与 `third_party/crawl4ai-main`
   - 遇到任何 bug / 功能需求，**第一步先到这两个仓库的源码中找官方解法**（类定义、方法实现、官方注释、examples/ 示例）
   - 只有源码里确实没有对应实现时，才自己设计，并要在工作日志注明"源码无对应，自研方案"

2. **官方 API 用法优先**
   - browser-use 的 `BrowserSession` 官方推荐直接传参：`BrowserSession(headless=..., user_data_dir=...)`，不要包一层 `BrowserProfile`
   - 嵌入 flet 等已有事件循环的应用时，`Agent(..., enable_signal_handler=False)`（官方注释明确说明）
   - **agent 引擎（app/agent_engine.py）浏览器层已完整移植官方栈**：
     - `BrowserSession` 启动（弹窗/登录态/持久化）
     - `DomService.get_serialized_dom_tree()` → `llm_representation(include_attributes=[class,id,href,title,src,alt,name,type,value])` 拿官方 Elements 视图（AI 看源代码）
     - `Element`（browser_use.actor.element）按 backend_node_id 操作（click/fill/hover）
     - `page.get_elements_by_css_selector` 官方 CSS 提取
   - **已知官方 bug（移植时验证发现）**：`Element.click()` 的坐标在 scrollIntoView **之前**计算，视口外元素会被 clamp 到错误位置 → 点击前先 `elem.evaluate("() => { this.scrollIntoView({block:'center'}); return true; }")` 再点

3. **测试先行**
   - 每个修复/新功能必须写独立测试脚本（`_test_*.py`）验证后再交付
   - 测试脚本放在项目根目录，可重复运行

## 环境

- Python: `D:\python\python.exe`（系统 3.13，已装 scrapling 0.4.15 + flet 0.86.5 + browser-use 0.13.8 + nest_asyncio + **crawl4ai 0.9.3**）
- **crawl4ai 安装注意**：`pip install crawl4ai==0.9.3 --no-deps` 后单独装缺失依赖（pyOpenSSL/pillow/nltk 等），避免与 scrapling/browser-use 冲突；现有栈 openai 2.16.0 完好
- 运行: `cd D:\workbuudy\Scrapling && D:\python\python.exe -m app.main`
- Web 模式: `run_web.py`（端口 8550）或 `启动智爬.bat`
- 参考源码: `third_party/browser-use-main`、`third_party/crawl4ai-main`（只读，勿删）

## 引擎调度（appengines.py）

- `_pick_engine_plan`：智能场景调度——「整站/所有页面/所有分类」等 → Crawl4AI BFS 深爬；普通页面 →auto 链（Scrapling →Crawl4AI →direct →agent →browser-use）
- 深爬关键词必须明确指向"多页面"（整站/全站/所有页面/所有分类/所有链接等），「所有书籍」不算深爬
- `run_pipeline` 支持 `**kwargs` 透传引擎专属参数（deep_max_depth/max_pages/cache_mode/use_llm_extraction），按签名过滤
- Crawl4AIEngine 用官方 `generate_schema(url, query, llm_config)` 让 DeepSeek 从真实页面生成精确 schema（等价 AI 看源代码找字段），失败回退通用 schema

## 融合引擎架构（app/unified_engine.py，**本项目核心创新**）

- 不是顺序回退，是**真正融合**：场景画像 → 并行竞争/管道协作 → 质量仲裁 → 字段融合
- 三大组件：
  - `ScenarioAnalyzer`：纯关键词分析
  - `LLMScenarioAnalyzer`：**LLM 推理 + 关键词降级**（融合引擎泛化方案 A）
  - `PlanBuilder`：根据场景画像生成 race/pipeline/enhance 计划
  - `QualityScorer`：行数/完整率/空值率/去重率综合评分
  - `Arbiter`：多引擎结果选优 + 字段级融合
- 三种执行模式：
  - **race 并行竞争**：多引擎同时跑，先出高分者胜（核心时间时间）
  - **pipeline 管道协作**：A 产出 → B 消费（如 agent 登录 → 提取引擎消费登录态）
  - **enhance 增强**：主结果 + 辅助引擎补字段
- `UnifiedEngine` 是统一入口（run_pipeline auto/unified 默认走融合引擎）
- 架构文档：`融合引擎架构文档.md`
- 关键修复：**litellm 环境变量 bug**（Crawl4AI/browser-use 调 AI 前必须 `os.environ["DEEPSEEK_API_KEY"] = key`，否则读不到正确的 key）

## 关键文件

- `app/engines.py` — 多引擎调度（scrapling/direct/browser-use/agent/crawl4ai）+ 智能场景选择
- `app/agent_engine.py` — 自研 AI 浏览器引擎（DeepSeek 主力，官方 BrowserSession+DomService+Element 栈）
- `app/ui/main_view.py` — 主界面（引擎下拉含 crawl4ai；深爬参数 auto 时也透传）
- `app/last_result.py` — 上次结果持久化（web 刷新不丢数据）
- `自查结果清单.md` — 功能自查报告（2026-09-01，56/56 通过）
- `app/scraper.py` / `llm.py` / `validator.py` / `history.py` / `exporter.py`
