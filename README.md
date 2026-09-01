    user_data_dir=r"D:\.Chrome-Data",
)
印刷(f"行数={伦（result.行）}， 发动机={result.engine}")
```
### 3.2 直接用 UnifiedEngine
```派森
来自app.unified_engine进口统一引擎
英文 UnifiedEngine（=无头=确实如此,user_data_dir=r"D:\.Chrome-Data")
结果 eng.run（=
    user_input="提取 Top250 电影名、评分、链接",
    网址="https://movie.douban.com/top250",
    api_key=API_KEY,
    进展=λ m:印刷（男），
)
```
### 3.3 场景分析器单独使用（不需要抓取）
```派森
来自app.unified_engine进口LLMScenarioAnalyzer
analyzer LLMScenarioAnalyzer（=api_key=API_KEY)
Prof analyzer.analyze（="需要登录的页面，提取用户列表","https://example.com/login")
印刷(f"情景={Scenario教授}， 信心={自信教授}")
印刷(f"理由：{教授原因}")
```
---
## 4. 与其他方案的对比
|方案|代表项目|调度方式|协作|泛化|
|---|---|---|---|---|
| **顺序回退** |大多数爬虫框架|串行（慢）|❌ 无|规则|
| **轮询调度** |自研简单调度|轮询|❌ 无|规则|
| **Crawl4AI（官方）**|爬行4AI|单引擎（BFS/DFS）|❌|规则|
| **浏览器使用** |浏览器使用|单引擎（Agent）|❌|灵活但慢|
| **路由+并行（融合）** | **本项目 UnifiedEngine** | **LLM 推理+并行竞争+仲裁** | **✅ 种族/流程/增强** | **✅ LLM 真正泛化** |
**融合引擎的独特价值：**
- **不是单引擎的加强版，而是6 个引擎的真正合体******
- **race/pipeline/enhance 三种模式支持复杂场景**
- **SharedContext 让引擎协作（如 agent 登录→提取引擎消费）**
- **字段级融合让不同引擎的字段取并集**
- **LLM 场景分析器让“说什么都能懂”，不用穷举关键词**
---
## 5. 实测场景汇总
|场景|指令|引擎组合|结果|
|---|---|---|---|
|静态页（书籍）|提取书籍标题价格|竞速（刮取，爬行4AI）|20 行，54 分|
|整站深爬（books）|爬所有分类|种族（crawl4ai）|487 行，90 分|
|整站深爬（笔趣阁）|整站爬取小说图片/名称/链接/作者|种族（crawl4ai）|19 行，53.8 分（LLM 自动识别 deep_crawl）****|
---
## 6. 调试与扩展
### 6.1 日志输出
每次融合引擎执行会输出：
```
🤖 LLM 场景：{scenario}（置信度 {confidence}）
📋 场景画像：{scenario}（置信度 {confidence}）;原因：{reasons}
🗺️ 执行计划：race（a+b） → enhance（c）
▶ 执行节点（race）： scrapling+crawl4ai
🌐 [共享上下文] HTML：12000字符 DOM视图：0字符 接口：0条 登录：否 selector：0个
📊 质量评分：54.0/100（20行，字段完整率 100%，去重率 100%）
```
### 6.2 添加新引擎
只需两步：
```派森
#1. 在 _ENGINE_MATRIX 注册新引擎
_ENGINE_MATRIX = [
    ...,
    {"名称":"my_new_engine","唱片公司":"我的新引擎","帽": {...},
     "速度":70,"成本":30,"可靠性":80,"needs_api_key":确实如此},
]
#2. 在 _engine_instances 实例化
防守 _engine_instances(...):
    回归 {
        ...,
        "my_new_engine"： MyEngine（），
    }
```
### 6.3 自定义 PlanBuilder
如需为特定场景自定义执行计划，只需重写`PlanBuilder.build（）`：
```派森
级别 定制规划构建器(规划构建器):
    防守 建造(自我,教授):
        如果Scenario教授== SCENARIO_DEEP:
            #自定义深爬：先 crawl4ai BFS，再用 agent 补登录态页面
            回归 ([
        [
PlanNode（模式="比赛",engine_names=["爬行4AI。"],暂停=300),
PlanNode（模式="增强",engine_names=["特工"],暂停=180),
]，prof.scenario）
        回归 超级（）.build（教授）
```
---
## 7.已知局限与未来工作
|局限|原因|改进方向|
|---|---|---|
| 大型语言模型余额不足时退到关键词|关键词词典封闭|加本地结构探测（基于 selectolax）|
|多个 race 引擎可能重复消耗人工智能 |并行调用|共享大型语言模型响应（加LRU缓存）|
|SharedContext 还没被引擎消费|时间紧|下一个迭代实现|
|浏览器引擎对反爬站仍可能失败|反爬无银弹|集成 stealthy 人类行为模拟+|
---
## 8.相关代码文件
- “appunified_engine.py”/（核心实现，800行）
- “appengines.py”/（入口集成 run_pipeline）
- “appagent_engine.py”/（人工智能浏览器引擎官方栈移植）
- “appllm.py”/（DeepSeek 客户端 litellm 环境变量修复）+
- “_test_unified_engine.py”（融合引擎完整测试）
- “_test_llm_scenario.py”（大型语言模型场景分析测试5/5通过）
- '自查结果清单.md'（六引擎自查报告）
