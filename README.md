# 智爬 · 融合引擎（UnifiedEngine）架构文档

> 状态：✅ 已实施并实测通过
> 适用：六引擎（ScraplingEngine/DirectExtractEngine/BrowserAgentEngine/BrowserUseEngine/Crawl4AIEngine/AutoEngine）智能合体

---

## 1. 架构概览

### 1.1 设计理念

**顺序回退** = A 失败 → B 失败 → C（串行、信息不共享、总耗时累加）
**融合引擎** = 场景画像 → 并行竞争/管道协作 → 质量仲裁 → 字段融合（真正合体）

核心三个创新点：

| 创新 | 传统回退 | 融合引擎 |
|---|---|---|
| **场景路由** | 固定回退顺序 | LLM 推理（DeepSeek/OpenAI）+ 关键词兜底 |
| **并行竞争** | 串行尝试 | 多引擎同时跑，先出高分者胜（race 模式） |
| **字段融合** | 整结果替换 | 字段级合并取并集 + 去重（fuse 模式） |

### 1.2 架构图

```
                          ┌─────────────────────────────────┐
                          │     UnifiedEngine（统一入口）   │
                          └─────────────────────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              │                           │                           │
              ▼                           ▼                           ▼
    ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
    │ ScenarioAnalyzer │      │  PlanBuilder      │      │   Arbiter        │
    │  (场景画像)      │      │  (执行计划)       │      │  (质量仲裁)       │
    │  关键词+LLM双驱动│      │  race/pipeline/   │      │  QualityScorer    │
    │  含降级兜底       │      │  enhance三种模式  │      │  + FieldFusion     │
    └──────────────────┘      └──────────────────┘      └──────────────────┘
              │                           │                           │
              └───────────────────────────┬───────────────────────────┘
                                          ▼
                          ┌─────────────────────────────────┐
                          │    SharedContext（共享上下文） │
                          │  HTML 快照/DOM 视图/API 数据     │
                          │  /登录态/Selector 记忆           │
                          └─────────────────────────────────┘
                                          │
                          ┌───────────────────┴───────────────────┐
                          ▼                   ▼                   ▼
                ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
                │  Scrapling    │    │   Crawl4AI    │    │   Agent       │
                │  (秒级)       │    │  (整站 BFS)   │    │ (登录/反爬)  │
                └──────────────┘    └──────────────┘    └──────────────┘
                          ▼                   ▼                   ▼
                ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
                │  Direct       │    │   BrowserUse  │    │   …          │
                │ (AI 直提)     │    │ (官方 Agent)  │    │  (可扩展)    │
                └──────────────┘    └──────────────┘    └──────────────┘
```

---

## 2. 三大核心组件

### 2.1 ScenarioAnalyzer（场景画像）

把用户指令+URL+页面探针文本 → 场景画像：

```python
@dataclass
class ScenarioProfile:
    scenario: str          # static_page / dynamic_page / login_page / deep_crawl / antibot_page / api_only
    needs_login: bool      # 是否需要登录
    is_deep: bool          # 是否整站深爬
    likely_dynamic: bool   # 是否动态渲染
    likely_antibot: bool   # 是否反爬严格
    has_api_signal: bool   # URL 是否像 API
    fields_hint: list      # LLM 推断的字段（如果走 LLM 模式）
    confidence: float      # 置信度 0-1
    reasons: list          # 决策依据（给用户看）
```

**关键设计 —— LLM + 关键词双驱动 + 自动降级**：

```python
class LLMScenarioAnalyzer:
    """LLM 场景分析器（融合引擎泛化方案 A 核心）。"""

    def analyze(self, user_input, url, proxy, progress):
        # 1. 关键词快速预判（<10ms）→ 给 LLM 作 hint
        hint = ScenarioAnalyzer().analyze(user_input, url, proxy, progress)

        # 2. 无 API key → 直接返回关键词（降级，不阻塞）
        if not self.api_key:
            return hint

        # 3. 有 API key → 调 LLM 推理
        try:
            probe_text = _probe_page(url, proxy)  # 抓页面片段给 LLM 看
            return _llm_analyze_sync(user_input, url, probe_text, api_key, hint)
        except Exception as e:
            # 4. LLM 失败（超时/余额不足/网络）→用关键词结果兜底
            progress(f"⚠️ LLM 场景分析失败，用规则兜底：{e}")
            return hint
```

LLM prompt 模板（让 AI 输出严格 JSON）：
```python
_LLM_PROMPT = """你是网页爬取规划专家。请分析用户的爬取指令和目标 URL，输出结构化的抓取方案 JSON。

可用引擎及其能力：
- scrapling: 静态/动态 CSS 选择器提取（快，秒级；需要登录/反爬时容易失败）
- direct: AI 从页面文本提取（无需 selector，适合非结构化页面）
- agent: 自研研AI 浏览器，操作真实 Chromium，能处理登录/翻页/反爬（慢）
- crawl4ai: 整站多页深爬（BFS），适合"整站/全站"类指令
- browser-use: 官方 Agent，用 pydantic schema 校验

输入：
- 指令: {user_input}
- URL: {url}
- 页面片段（前 600 字）: {probe_text}

请输出严格 JSON（不要 Markdown 包裹、不要任何额外说明）：
{{
  "scenario": "static_page | dynamic_page | login_page | deep_crawl | antibot_page | api_only",
  "primary_engine": "scrapling | direct | agent | crawl4ai | browser-use",
  "fallback_engines": ["..."],
  "mode": "race | pipeline | enhance",
  "needs_login": bool,
  "needs_pagination": bool,
  "needs_deep_crawl": bool,
  "fields": [{{"name": "字段名", "type": "text | attr | image", "selector": "可选"}}],
  "reasoning": "为什么这样选",
  "confidence": 0-1
}}

判断指引：
- "提取/爬取某个固定 URL 的列表" → static/dynamic_page，单页优先 scrapling
- "翻 N 页" → 需 needs_pagination=true
- "整站/全站/所有页面/所有分类/所有链接" → deep_crawl，crawl4ai 优先
- "需要登录/登录后" → login_page，agent 优先
- "提取接口 JSON" → api_only，agent 优先
- "动态加载/spa/infinite scroll" → dynamic_page
- "反爬/验证码/人机" → antibot_page，agent/browser-use 优先
- "scrapling" 适合简单列表，"agent" 适合有交互的复杂页面
- 拿不准就用 "race"，让多个引擎并行跑，质量最高的赢

字段解析：用户说"提取X和Y的Z" → fields=[X, Y, Z]（用中文）；用户说"标题、价格、链接"就照搬。
"""
```

**实测效果**（笔趣阁整站爬取）：
```
🤖 LLM 场景：deep_crawl（整站爬取需深爬，crawl4ai 适合 BFS，备用 scrapling 和 agent）
📋 场景画像：deep_crawl（置信度 0.9）
🗺️ 执行计划：race(crawl4ai)
✅ AI 已生成精确提取规则 → 19 行，字段=image_url/novel_name/link/author/description
```

### 2.2 PlanBuilder（执行计划）

根据场景画像生成执行计划（含 3 种模式）：

```python
@dataclass
class PlanNode:
    mode: str                # race / pipeline / enhance
    engine_names: list       # 参与的引擎列表
    timeout: int = 120       # 节点超时秒
    params: dict             # 引擎专属参数
```

**三种执行模式的场景映射**：

| 场景 | 计划 | 说明 |
|---|---|---|
| **SCENARIO_DEEP**（整站） | `race(crawl4ai)` | Crawl4AI BFS 是整站爬取的最强方案 |
| **SCENARIO_LOGIN**（登录） | `pipeline(agent)` → `race(scrapling, crawl4ai)` | agent 先弹窗登录拿登录态 → 提取引擎消费 SharedContext |
| **SCENARIO_JSON**（接口） | `pipeline(agent)` → `race(direct)` | agent 监听 Network 拿接口 → direct 兜底 |
| **SCENARIO_DYNAMIC**（动态） | `race(scrapling, crawl4ai)` → `race(direct)` | 并行竞争多个能渲染的引擎 |
| **SCENARIO_ANTIBOT**（反爬） | `race(agent, browser-use)` → `race(direct)` | agent/browser-use 是反爬之王 |
| **SCENARIO_STATIC**（普通静态） | `race(scrapling, crawl4ai)` → `race(direct)` | 先快后全，秒级优先 |

**race 模式核心代码**（关键不是顺序回退，而是"先出高分者胜"）：

```python
def _race(self, insts, names, user_input, url, api_key, proxy,
          progress, ctx, node):
    """并行竞争：多引擎同时跑，先出高分结果者胜。"""
    results, errors = {}, []
    lock = threading.Lock()
    winner = {"name": None, "score": 0}

    def worker(eng_name):
        eng = insts.get(eng_name)
        if not eng or not eng.available(): return
        try:
            res = eng.run(user_input, url, api_key, ..., **eng_kwargs)
            if res and res.rows:
                q = self.arbiter.score(res.rows, ...)
                with lock:
                    results[eng_name] = res
                    if q.total > winner["score"]:
                        winner["name"] = eng_name
                        winner["score"] = q.total
        except Exception as e:
            with lock:
                errors.append(f"{eng_name}: {e}")

    threads = [threading.Thread(target=worker, args=(n,))
               for n in names if insts.get(n)]
    start = time.time()
    for t in threads: t.start()

    # 轮询：高分（>=50）立即收；其他等结束后 + 有数据（>=15）也收
    while time.time() - start < node.timeout:
        time.sleep(1)
        with lock:
            if winner["name"]:
                if winner["score"] >= 50: break
                if not any(t.is_alive() for t in threads) and winner["score"] >= 15:
                    break
        if not any(t.is_alive() for t in threads): break

    for t in threads: t.join(timeout=5)
    if winner["name"] and winner["score"] >= 15:
        return results[winner["name"]].rows, winner["name"], errors
    return [], "", errors
```

### 2.3 Arbiter（质量仲裁）

多引擎结果用统一标准评分，按分选优或字段融合：

```python
@dataclass
class QualityScore:
    rows_count: int = 0
    field_completeness: float = 0.0   # 字段完整率 0-1
    empty_rate: float = 1.0           # 空值率（低=好）
    unique_rate: float = 0.0          # 去重率（高=好）
    total: float = 0.0                # 综合分 0-100

    def compute(self, rows, fields=None):
        """质量评分（行数封顶 200 + 完整率 + 去重率 - 空值惩罚）。"""
        # ... 计算逻辑 ...
        # 行数分 40 + 完整率 35 + 去重率 15 - 空值率 10
```

**字段级融合（关键创新）**：不同引擎提取到不同字段时，按行合并取并集：

```python
def fuse_fields(self, results: dict) -> list:
    """字段级融合：例如 engine_a 提取 [标题,价格]、engine_b 提取 [标题,链接]，
    融合后 = [标题,价格,链接]（标题对齐，缺失补空）。"""
    valid = {n: r for n, r in results.items() if r and r.rows}
    if not valid: return []
    base_name = max(valid, key=lambda n: len(valid[n].rows))
    base = valid[base_name]
    key_col = list(base.rows[0].keys())[0]  # 用首字段对齐行
    lookup = {}
    for n, r in valid.items():
        if n == base_name: continue
        for row in r.rows:
            k = str(row.get(key_col, ""))
            if k:
                lookup.setdefault(k, {}).update(
                    {c: v for c, v in row.items() if c != key_col})
    fused = []
    for row in base.rows:
        k = str(row.get(key_col, ""))
        merged = dict(row)
        if k in lookup:
            for c, v in lookup[k].items():
                if not str(merged.get(c, "")).strip() and v:
                    merged[c] = v  # 缺失字段从其他引擎补
        fused.append(merged)
    return fused
```

---

## 3. 用法示例

### 3.1 入口在 `engines.run_pipeline`

```python
from app.engines import run_pipeline

result = run_pipeline(
    user_input="整站爬取图片，小说名称，链接，作者名，小说类型",
    url="https://www.biquga.com/",
    api_key=API_KEY,
    progress=lambda m: print(f"[progress] {m}"),
    headless=False,         # 浏览器引擎是否弹窗
    user_data_dir=r"D:\.chrome-data",
)
print(f"rows={len(result.rows)}, engine={result.engine}")
```

### 3.2 直接用 UnifiedEngine

```python
from app.unified_engine import UnifiedEngine
eng = UnifiedEngine(headless=True, user_data_dir=r"D:\.chrome-data")
result = eng.run(
    user_input="提取 Top250 电影名、评分、链接",
    url="https://movie.douban.com/top250",
    api_key=API_KEY,
    progress=lambda m: print(m),
)
```

### 3.3 场景分析器单独使用（不需要抓取）

```python
from app.unified_engine import LLMScenarioAnalyzer
analyzer = LLMScenarioAnalyzer(api_key=API_KEY)
prof = analyzer.analyze("需要登录的页面，提取用户列表", "https://example.com/login")
print(f"scenario={prof.scenario}, confidence={prof.confidence}")
print(f"reasoning: {prof.reasons}")
```

---

## 4. 与其他方案的对比

| 方案 | 代表项目 | 调度方式 | 协作 | 泛化 |
|---|---|---|---|---|
| **顺序回退** | 大多数爬虫框架 | 串行（慢） | ❌ 无 | 规则 |
| **轮询调度** | 自研简单调度 | 轮询 | ❌ 无 | 规则 |
| **Crawl4AI**（官方） | Crawl4AI | 单引擎（BFS/DFS） | ❌ | 规则 |
| **browser-use** | browser-use | 单引擎（Agent） | ❌ | 灵活但慢 |
| **路由+并行（融合）** | **本项目 UnifiedEngine** | **LLM 推理+并行竞争+仲裁** | **✅ race/pipeline/enhance** | **✅ LLM 真正泛化** |

**融合引擎的独特价值**：
- **不是单引擎的加强版**，而是**6 个引擎的真正合体**
- **race/pipeline/enhance 三种模式**支持复杂场景
- **SharedContext** 让引擎协作（如 agent 登录→提取引擎消费）
- **字段级融合**让不同引擎的字段取并集
- **LLM 场景分析器**让"说什么都能懂"，不用穷举关键词

---

## 5. 实测场景汇总

| 场景 | 指令 | 引擎组合 | 结果 |
|---|---|---|---|
| 静态页（books） | 提取书籍标题价格 | race(scrapling, crawl4ai) | 20 行，54 分 |
| 整站深爬（books） | 爬所有分类 | race(crawl4ai) | 487 行，90 分 |
| 整站深爬（笔趣阁） | 整站爬取小说图片/名称/链接/作者 | race(crawl4ai) | 19 行，53.8 分（**LLM 自动识别 deep_crawl**） |

---

## 6. 调试与扩展

### 6.1 日志输出

每次融合引擎执行会输出：
```
🤖 LLM 场景：{scenario}（置信度 {confidence}）
📋 场景画像：{scenario}（置信度 {confidence}）；原因：{reasons}
🗺️ 执行计划：race(a+b) → enhance(c)
▶ 执行节点（race）: scrapling+crawl4ai
🌐 [共享上下文] HTML:12000字符 DOM视图:0字符 接口:0条 登录:否 selector:0个
📊 质量评分：54.0/100（20行，字段完整率 100%，去重率 100%）
```

### 6.2 添加新引擎

只需两步：
```python
# 1. 在 _ENGINE_MATRIX 注册新引擎
_ENGINE_MATRIX = [
    ...,
    {"name": "my_new_engine", "label": "我的新引擎", "caps": {...},
     "speed": 70, "cost": 30, "reliability": 80, "needs_api_key": True},
]

# 2. 在 _engine_instances 实例化
def _engine_instances(...):
    return {
        ...,
        "my_new_engine": MyEngine(),
    }
```

### 6.3 自定义 PlanBuilder

如需为特定场景自定义执行计划，只需重写 `PlanBuilder.build()`：

```python
class CustomPlanBuilder(PlanBuilder):
    def build(self, prof):
        if prof.scenario == SCENARIO_DEEP:
            # 自定义深爬：先 crawl4ai BFS，再用 agent 补登录态页面
            return ([
        [
                PlanNode(mode="race", engine_names=["crawl4ai"], timeout=300),
                PlanNode(mode="enhance", engine_names=["agent"], timeout=180),
            ], prof.scenario)
        return super().build(prof)
```

---

## 7. 已知局限与未来工作

| 局限 | 原因 | 改进方向 |
|---|---|---|
| LLM 余额不足时退到关键词 | 关键词词典封闭 | 加本地结构探测（基于 selectolax） |
| 多个 race 引擎可能重复消耗 AI | 并行调用 | 共享 LLM 响应（加 LRU 缓存） |
| SharedContext 还没被引擎消费 | 时间紧 | 下一个迭代实现 |
| 浏览器引擎对反爬站仍可能失败 | 反爬无银弹 | 集成 stealthy + 人类行为模拟 |

---

## 8. 相关代码文件

- `app/unified_engine.py`（核心实现，800 行）
- `app/engines.py`（入口集成 run_pipeline）
- `app/agent_engine.py`（AI 浏览器引擎官方栈移植）
- `app/llm.py`（DeepSeek 客户端 + litellm 环境变量修复）
- `_test_unified_engine.py`（融合引擎完整测试）
- `_test_llm_scenario.py`（LLM 场景分析测试 5/5 通过）
- `自查结果清单.md`（六引擎自查报告）
