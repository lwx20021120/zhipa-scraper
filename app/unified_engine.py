# -*- coding: utf-8 -*-
"""融合引擎（UnifiedEngine）：六引擎真正合体，非顺序回退。

设计理念（参考 Firecrawl 多模式路由 + Dispatcher 模式调研）：
  顺序回退 = A失败→B失败→C（串行、无信息共享、浪费时间）
  融合引擎 = 分析场景 → 并行竞争/管道协作 → 质量仲裁 → 字段融合

核心组件：
1. EngineCapability —— 每个引擎声明能力（static/dynamic/antibot/
   interactive/deep/json_api/llm/selector），供路由匹配
2. ScenarioAnalyzer —— 综合分析指令+URL+快速探针 → 场景画像
3. PlanBuilder —— 画像 → 执行计划（并行组/管道链/增强组）
4. QualityScorer —— 统一质量评分（行数/字段完整率/空值率/去重率）
5. SharedContext —— 引擎间共享中间产物（HTML 快照/DOM 视图/API 数据）
6. Arbiter —— 冲突仲裁：多引擎结果按质量选优或字段级融合
7. UnifiedEngine —— 对外统一入口

执行模式：
- RACE 并行竞争：多个引擎同时跑，先出高分结果者胜（时间换胜率）
- PIPELINE 管道协作：A 的产出作为 B 的输入（如 agent 登录渲染 → 提取引擎消费）
- ENHANCE 增强：主引擎出结果，辅助引擎补充缺失字段
"""
import json
import threading
import time
import traceback
from dataclasses import dataclass, field

from .scraper import ScrapeError
# 模块级导入便于测试时 patch（融合引擎核心 AI 调用）
from .llm import _call_llm, _parse_json

# ---------- 能力声明 ----------

CAP_STATIC = "static"          # 静态页直接抓取
CAP_DYNAMIC = "dynamic"        # 动态渲染
CAP_ANTIBOT = "antibot"        # 反爬/伪装
CAP_INTERACTIVE = "interactive"  # 交互（点击/输入/翻页）
CAP_DEEP = "deep"              # 整站深爬
CAP_JSON_API = "json_api"      # 接口直取
CAP_LLM = "llm"                # AI 提取
CAP_SELECTOR = "selector"      # CSS/XPath 选择器
CAP_LOGIN = "login"            # 登录/验证码处理

# 场景画像
SCENARIO_STATIC = "static_page"      # 普通静态页
SCENARIO_DYNAMIC = "dynamic_page"    # 动态渲染页
SCENARIO_ANTIBOT = "antibot_page"    # 反爬页
SCENARIO_DEEP = "deep_crawl"         # 整站深爬
SCENARIO_LOGIN = "login_page"        # 登录墙
SCENARIO_JSON = "json_api"           # 接口直取


@dataclass
class EngineCapability:
    """引擎能力声明：引擎能干什么、擅长什么、代价是什么。"""
    name: str
    label: str
    capabilities: set          # 能力标签集合
    speed: int = 50            # 速度分 0-100（高=快）
    cost: int = 30             # 成本分 0-100（高=贵/耗资源）
    reliability: int = 50      # 可靠性分 0-100
    needs_api_key: bool = False


@dataclass
class ScenarioProfile:
    """场景画像：分析结果。"""
    scenario: str = SCENARIO_STATIC
    needs_login: bool = False
    is_deep: bool = False
    likely_dynamic: bool = False
    likely_antibot: bool = False
    has_api_signal: bool = False
    fields_hint: list = field(default_factory=list)
    confidence: float = 0.5
    reasons: list = field(default_factory=list)


@dataclass
class PlanNode:
    """执行计划节点：一组引擎 + 执行模式。"""
    mode: str                   # race / pipeline / enhance
    engine_names: list          # 参与引擎
    timeout: int = 120          # 超时秒
    params: dict = field(default_factory=dict)  # 附加参数


@dataclass
class Plan:
    """执行计划：有序节点列表。"""
    nodes: list = field(default_factory=list)
    scenario: str = SCENARIO_STATIC


@dataclass
class QualityScore:
    """质量评分。"""
    rows_count: int = 0
    field_completeness: float = 0.0   # 字段完整率 0-1
    empty_rate: float = 1.0           # 空值率 0-1（低=好）
    unique_rate: float = 0.0          # 去重率 0-1（高=好）
    total: float = 0.0                # 综合分 0-100

    def compute(self, rows, fields=None):
        """计算质量分。"""
        self.rows_count = len(rows)
        if not rows:
            self.total = 0.0
            return self
        # 字段完整率：有内容的字段占比
        cols = list(rows[0].keys())
        if fields:
            cols = [f.get("name", "") for f in fields] or cols
        filled = 0
        total_cells = 0
        for r in rows[:50]:  # 抽样 50 行
            for c in cols:
                total_cells += 1
                if str(r.get(c, "")).strip():
                    filled += 1
        self.field_completeness = filled / max(total_cells, 1)
        # 空值率
        empty = 0
        for r in rows[:50]:
            for c in cols:
                if not str(r.get(c, "")).strip():
                    empty += 1
        self.empty_rate = empty / max(total_cells, 1)
        # 去重率
        if rows and cols:
            keys = {str(tuple(r.get(c, "") for c in cols[:2]))
                    for r in rows}
            self.unique_rate = len(keys) / max(len(rows), 1)
        # 综合分：行数（封顶 200 行算满分）+ 完整率 + 去重率 - 空值惩罚
        row_score = min(self.rows_count / 200, 1.0) * 40
        complete_score = self.field_completeness * 35
        unique_score = self.unique_rate * 15
        empty_penalty = self.empty_rate * 10
        self.total = round(row_score + complete_score + unique_score
                           - empty_penalty, 1)
        return self


class SharedContext:
    """共享上下文：引擎间传递中间产物（融合的关键）。"""

    def __init__(self):
        self.html_snapshot = ""       # 页面 HTML 快照（谁拿到谁共享）
        self.dom_view = ""            # 渲染后 DOM 视图（agent 引擎产出）
        self.api_data = []            # 拦截的接口数据（Network 面板）
        self.login_ok = False         # 登录是否已成功
        self.selectors = {}           # 已验证的 selector（字段名→selector）
        self.schema = None            # Crawl4AI schema
        self._lock = threading.Lock()

    def set_html(self, html):
        with self._lock:
            if len(html) > len(self.html_snapshot):
                self.html_snapshot = html

    def set_dom_view(self, dom):
        with self._lock:
            if dom and len(dom) > len(self.dom_view):
                self.dom_view = dom

    def add_api(self, data):
        with self._lock:
            self.api_data.append(data)

    def remember_selector(self, field, selector):
        with self._lock:
            self.selectors[field] = selector

    def summary(self) -> str:
        return (f"[共享上下文] HTML:{len(self.html_snapshot)}字符 "
                f"DOM视图:{len(self.dom_view)}字符 "
                f"接口:{len(self.api_data)}条 "
                f"登录:{'是' if self.login_ok else '否'} "
                f"selector:{len(self.selectors)}个")


# ---------- 场景分析 ----------

DEEP_KEYWORDS = ("整站", "全站", "所有页面", "全部页面", "整个网站",
                 "所有分类", "全部分类", "所有链接", "全部链接",
                 "所有文章", "所有新闻", "所有帖子", "所有栏目",
                 "全部栏目", "网站所有", "爬取整个", "遍历全站", "全站所有")
DYNAMIC_KEYWORDS = ("动态", "加载更多", "无限滚动", "滚动加载", "javascript",
                    "js 渲染", "vue", "react", "spa")
LOGIN_KEYWORDS = ("登录", "需要登录", "验证码", "滑块", "扫码登录", "登录墙")
API_KEYWORDS = ("接口", "api", "json 数据", "network")
ANTIBOT_KEYWORDS = ("反爬", "反爬虫", "封禁", "风控", "滑块", "人机验证")

PROBE_FETCHERS = ("static", "dynamic")  # 探针抓取顺序


def _probe_page(url: str, proxy: str = "") -> str:
    """快速探针：尝试抓取页面看是否静态可达（融合路由的第一步）。

    只做 1 次快速尝试（5 秒超时），失败不重试——探针的目的是
    快速判断页面类型，不是真正抓取。
    """
    import logging
    from .scraper import fetch_page
    # 临时降低 scrapling 日志级别，避免刷屏
    old_level = logging.getLogger("scrapling").level
    logging.getLogger("scrapling").setLevel(logging.ERROR)
    try:
        for fetcher in PROBE_FETCHERS:
            try:
                page = fetch_page(url, fetcher, 2, proxy)
                body = getattr(page, "body", "") or ""
                if isinstance(body, bytes):
                    body = body.decode("utf-8", "ignore")
                if len(body.strip()) > 200:
                    return body
            except Exception:
                continue
        return ""
    finally:
        logging.getLogger("scrapling").setLevel(old_level)


def _looks_login(text: str) -> bool:
    t = (text or "").lower()
    markers = ("登录", "扫码", "请输入验证码", "人机验证", "滑块",
               "请完成验证", "sign in", "login", "captcha", "qr code")
    return sum(1 for m in markers if m in t) >= 2


def _looks_empty(text: str) -> bool:
    """页面 HTML 拿到但几乎没内容 → 动态渲染特征。"""
    t = (text or "").strip()
    if len(t) < 500:
        return True
    # 去除 script/style 后看正文长度
    import re
    body = re.sub(r"<(script|style)[^>]*>.*?</\\1>", "", t, flags=re.S)
    body = re.sub(r"<[^>]+>", "", body)
    return len(body.strip()) < 50


class ScenarioAnalyzer:
    """场景分析器：指令 + URL + 探针 → 场景画像。"""

    def analyze(self, user_input: str, url: str = "", proxy: str = "",
                progress=None) -> ScenarioProfile:
        prof = ScenarioProfile()
        reasons = []

        # 1. 指令信号
        ui = user_input or ""
        if any(k in ui for k in DEEP_KEYWORDS):
            prof.is_deep = True
            reasons.append("指令含整站关键词")
        if any(k in ui for k in DYNAMIC_KEYWORDS):
            prof.likely_dynamic = True
            reasons.append("指令含动态关键词")
        if any(k in ui for k in LOGIN_KEYWORDS):
            prof.needs_login = True
            reasons.append("指令含登录关键词")
        if any(k in ui for k in API_KEYWORDS):
            prof.has_api_signal = True
            reasons.append("指令含接口关键词")
        if any(k in ui for k in ANTIBOT_KEYWORDS):
            prof.likely_antibot = True
            reasons.append("指令含反爬关键词")

        # 2. URL 信号
        if url:
            low = url.lower()
            if any(d in low for d in (".asp", ".aspx", ".php", ".jsp")):
                prof.likely_dynamic = True
                reasons.append("URL 含服务端脚本后缀")
            if "api" in low or "/v1/" in low or "/v2/" in low:
                prof.has_api_signal = True
                reasons.append("URL 像 API 端点")

        # 3. 探针（真实抓一次判断）
        probe_text = ""
        if url and not prof.is_deep:
            try:
                if progress:
                    progress("🔍 探针：正在快速探测页面类型…")
                probe_text = _probe_page(url, proxy)
                if probe_text:
                    if _looks_login(probe_text):
                        prof.needs_login = True
                        reasons.append("探针发现登录墙")
                    elif _looks_empty(probe_text):
                        prof.likely_dynamic = True
                        reasons.append("探针发现页面空（动态渲染）")
                    else:
                        reasons.append("探针发现静态可抓")
            except Exception:
                pass

        # 4. 综合判定场景
        if prof.needs_login:
            prof.scenario = SCENARIO_LOGIN
            prof.confidence = 0.85
        elif prof.is_deep:
            prof.scenario = SCENARIO_DEEP
            prof.confidence = 0.9
        elif prof.has_api_signal:
            prof.scenario = SCENARIO_JSON
            prof.confidence = 0.6
        elif prof.likely_antibot or (probe_text and _looks_empty(probe_text)
                                     and not prof.likely_dynamic):
            prof.scenario = SCENARIO_ANTIBOT
            prof.confidence = 0.7
        elif prof.likely_dynamic:
            prof.scenario = SCENARIO_DYNAMIC
            prof.confidence = 0.7
        else:
            prof.scenario = SCENARIO_STATIC
            prof.confidence = 0.6 if probe_text else 0.4

        prof.reasons = reasons
        return prof


# ---------- LLM 场景分析器（融合引擎泛化方案 A 核心） ----------

# LLM 输出的场景画像 schema（也是给 LLM 的指令）
_LLM_PROMPT = """你是网页爬取规划专家。请分析用户的爬取指令和目标 URL，输出**结构化的抓取方案 JSON**。

可用引擎及其能力：
- scrapling: 静态/动态 CSS 选择器提取（快，秒级；需要登录/反爬时容易失败）
- direct: AI 从页面文本提取（无需 selector，适合非结构化页面）
- agent: 自研 AI 浏览器，操作真实 Chromium，能处理登录/翻页/反爬（慢）
- crawl4ai: 整站多页深爬（BFS），适合"整站/全站"类指令
- browser-use: 官方 Agent，用 pydantic schema 校验（与某些 LLM 不兼容）

输入：
- 指令: {user_input}
- URL: {url}
- 页面片段（前 600 字）: {probe_text}

请输出**严格 JSON**（不要 Markdown 包裹、不要任何额外说明）：
{{
  "scenario": "static_page | dynamic_page | login_page | deep_crawl | antibot_page | api_only",
  "primary_engine": "scrapling | direct | agent | crawl4ai | browser-use",
  "fallback_engines": ["..."],
  "mode": "race | pipeline | enhance",
  "needs_login": bool,
  "needs_pagination": bool,
  "needs_deep_crawl": bool,
  "fields": [{{"name": "字段名（中文）", "type": "text | attr | image", "selector": "可选"}}],
  "reasoning": "为什么这样选（30 字内）",
  "confidence": 0-1
}}

判断指引：
- "提取/爬取某个固定URL 的列表" → static/dynamic_page，单页优先 scrapling
- "翻 N页" → 需 needs_pagination=true，仍是单站
- "整站/全站/所有页面/所有分类/所有链接" → deep_crawl，crawl4ai 优先
- "需要登录/登录后" → login_page，agent 优先（弹窗登录）
- "提取接口 JSON" → api_only，agent 优先（监听 Network）
- "动态加载/spa/infinite scroll" → dynamic_page
- "反爬/验证码/人机" → antibot_page，agent/browser-use 优先
- "scrapling" 适合简单列表，"agent" 适合有交互的复杂页面
- 拿不准就用 "race"，让多个引擎并行跑，质量最高的赢

字段解析：用户说"提取X和Y的Z" → fields=[X, Y, Z]（用中文）；用户说"标题、价格、链接"就照搬。
"""


def _llm_analyze_sync(user_input, url, probe_text, api_key, hint):
    """调 LLM 分析场景，返回 ScenarioProfile（LLM 失败抛异常）。"""
    prompt = _LLM_PROMPT.format(
        user_input=user_input or "",
        url=url or "",
        probe_text=(probe_text or "")[:600],
    )
    raw = _call_llm([
        {"role": "system",
         "content": "你是网页爬取规划专家，输出严格 JSON，不要任何额外说明。"},
        {"role": "user", "content": prompt},
    ], api_key)
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM 输出无法解析：{raw[:120]}")

    # 转换为 ScenarioProfile（兼容现有 PlanBuilder）
    scenario_map = {
        "static_page": SCENARIO_STATIC,
        "dynamic_page": SCENARIO_DYNAMIC,
        "antibot_page": SCENARIO_ANTIBOT,
        "login_page": SCENARIO_LOGIN,
        "deep_crawl": SCENARIO_DEEP,
        "api_only": SCENARIO_JSON,
    }
    scenario = scenario_map.get(parsed.get("scenario", ""), hint.scenario)
    needs_login = bool(parsed.get("needs_login", False))
    needs_pagination = bool(parsed.get("needs_pagination", False))
    needs_deep = bool(parsed.get("needs_deep_crawl",
                          scenario == SCENARIO_DEEP))
    # 关键修复：用户说"前N页/翻N页" → LLM 推断 needs_pagination=true
    # → 也走深爬（避免被 race 里 scrapling 6 行首页抢跑）
    if needs_pagination and not needs_deep:
        needs_deep = True
        if scenario == SCENARIO_DYNAMIC:
            scenario = SCENARIO_DEEP
    # 字段信息
    fields = parsed.get("fields") or []
    if fields:
        hint.fields_hint = [
            {"name": f.get("name", "字段"),
             "selector": f.get("selector", ""),
             "type": f.get("type", "text"),
             "attr": (f.get("attribute", "") if f.get("type") == "attr"
                      else "")}
            for f in fields if isinstance(f, dict) and f.get("name")
        ]
    return ScenarioProfile(
        scenario=scenario,
        needs_login=needs_login,
        is_deep=needs_deep,
        likely_dynamic=scenario == SCENARIO_DYNAMIC,
        likely_antibot=scenario == SCENARIO_ANTIBOT,
        has_api_signal=scenario == SCENARIO_JSON,
        fields_hint=hint.fields_hint,
        confidence=float(parsed.get("confidence", 0.6)),
        reasons=[parsed.get("reasoning", "LLM 推理")] +
                  ([f"LLM 提示: {parsed['key']}"] if parsed.get("key") else []),
    )


class LLMScenarioAnalyzer:
    """LLM 场景分析器：真正泛化（不依赖固定关键词）。

    工作流程：
    1. 关键词快速预判（<10ms）→ 给 LLM 作 hint
    2. 无 API key → 直接返回关键词结果（降级）
    3. 有 API key → 调 LLM 推理（DeepSeek/OpenAI/任意兼容 OpenAI 的）
    4. LLM 失败 → 降级到关键词结果（用户体验无感）

    输出与 ScenarioAnalyzer 完全一致：ScenarioProfile。
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._fallback = ScenarioAnalyzer()

    def analyze(self, user_input, url="", proxy="",
                progress=None) -> ScenarioProfile:
        # 1. 关键词预判（hint）
        try:
            hint = self._fallback.analyze(user_input, url, proxy, progress)
        except Exception:
            hint = ScenarioProfile()

        # 2. 无 API key → 直接返回关键词
        if not self.api_key:
            return hint

        # 3. 有 API key → 调 LLM 推理
        # 先做轻量探针（让 LLM 看到真实页面片段，决策更准）
        probe_text = ""
        if url:
            try:
                probe_text = _probe_page(url, proxy)
            except Exception:
                pass
        try:
            if progress:
                progress("🤖 LLM 场景分析：让 AI 理解指令+URL+页面…")
            prof = _llm_analyze_sync(user_input, url, probe_text,
                                     self.api_key, hint)
            if progress:
                progress(f"🤖 LLM 场景：{prof.scenario}（{prof.reasons[0]}）"
                         if prof.reasons else
                         f"🤖 LLM 场景：{prof.scenario}")
            return prof
        except Exception as e:
            # 4. LLM 失败 → 降级到关键词（不阻塞用户）
            if progress:
                progress(f"⚠️ LLM 场景分析失败，用规则兜底：{type(e).__name__}: "
                         f"{str(e)[:80]}")
            return hint


# ---------- 执行计划 ----------

def _engines_by_cap(cap: str) -> list:
    """按能力找引擎（能力矩阵查询）。"""
    return [e["name"] for e in _ENGINE_MATRIX if cap in e["caps"]]


class PlanBuilder:
    """根据场景画像生成执行计划。

    关键设计——不是顺序回退，而是：
    - RACE：多引擎并行竞争，先出高分者胜
    - PIPELINE：引擎协作（A 产出 → B 消费）
    - ENHANCE：主引擎出结果，辅助引擎补字段
    """

    def build(self, prof: ScenarioProfile) -> Plan:
        plan = Plan(scenario=prof.scenario)

        if prof.scenario == SCENARIO_DEEP:
            # 整站深爬：Crawl4AI BFS 为主
            plan.nodes.append(PlanNode(
                mode="race",
                engine_names=["crawl4ai"],
                timeout=300,
                params={"deep_max_depth": 2, "max_pages": 40},
            ))
        elif prof.scenario == SCENARIO_LOGIN:
            # 登录墙：agent 引擎（有头弹窗登录）→ 成功后提取
            plan.nodes.append(PlanNode(
                mode="pipeline",
                engine_names=["agent"],
                timeout=180,
                params={"headless": False},
            ))
            plan.nodes.append(PlanNode(
                mode="race",
                engine_names=["scrapling", "crawl4ai"],
                timeout=120,
            ))
        elif prof.scenario == SCENARIO_JSON:
            # 接口型：agent 引擎抓 Network → 直提兜底
            plan.nodes.append(PlanNode(
                mode="pipeline",
                engine_names=["agent"],
                timeout=150,
            ))
            plan.nodes.append(PlanNode(
                mode="race",
                engine_names=["direct"],
                timeout=90,
            ))
        elif prof.scenario == SCENARIO_DYNAMIC:
            # 动态页：并行竞争（crawl4ai 渲染 vs agent 浏览器）
            # 关键修复：如果 profile.is_deep=True（用户要"前N页"等多页深爬），
            # 不再 race scrapling（秒级首页 6 行抢跑覆盖 慢 BFS 多页结果），
            # 只跑 crawl4ai BFS 拿到所有页
            if prof.is_deep:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["crawl4ai"],
                    timeout=300,
                    params={"deep_max_depth": 1, "max_pages": 10},
                ))
            else:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["scrapling", "crawl4ai"],
                    timeout=120,
                ))
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["direct"],
                    timeout=90,
                ))
        elif prof.scenario == SCENARIO_ANTIBOT:
            # 反爬页：agent 引擎（伪装浏览器 + DOM 视图）
            # is_deep 时也只跑 crawl4ai（避免 scrapling 抢跑）
            if prof.is_deep:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["crawl4ai"],
                    timeout=300,
                    params={"deep_max_depth": 1, "max_pages": 10},
                ))
            else:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["agent", "browser-use"],
                    timeout=180,
                ))
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["direct"],
                    timeout=90,
                ))
        else:
            # 静态页：最快引擎并行竞争
            # is_deep 时只跑 crawl4ai（避免 scrapling 抢跑）
            if prof.is_deep:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["crawl4ai"],
                    timeout=300,
                    params={"deep_max_depth": 1, "max_pages": 10},
                ))
            else:
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["scrapling", "crawl4ai"],
                    timeout=90,
                ))
                plan.nodes.append(PlanNode(
                    mode="race",
                    engine_names=["direct"],
                    timeout=60,
                ))

        return plan


# ---------- 引擎注册表 ----------

_ENGINE_MATRIX = [
    {"name": "scrapling", "label": "selector 引擎", "caps": {
        CAP_STATIC, CAP_SELECTOR, CAP_DYNAMIC}, "speed": 90, "cost": 10,
     "reliability": 70, "needs_api_key": True},
    {"name": "direct", "label": "AI 直提引擎", "caps": {
        CAP_LLM, CAP_STATIC, CAP_DYNAMIC}, "speed": 60, "cost": 40,
     "reliability": 75, "needs_api_key": True},
    {"name": "browser-use", "label": "browser-use 引擎", "caps": {
        CAP_INTERACTIVE, CAP_LOGIN, CAP_ANTIBOT, CAP_DYNAMIC},
     "speed": 30, "cost": 80, "reliability": 60, "needs_api_key": True},
    {"name": "agent", "label": "AI 浏览器引擎", "caps": {
        CAP_INTERACTIVE, CAP_LOGIN, CAP_ANTIBOT, CAP_DYNAMIC,
        CAP_JSON_API}, "speed": 25, "cost": 70, "reliability": 80,
     "needs_api_key": True},
    {"name": "crawl4ai", "label": "Crawl4AI 引擎", "caps": {
        CAP_DEEP, CAP_SELECTOR, CAP_STATIC, CAP_DYNAMIC, CAP_LLM},
     "speed": 50, "cost": 50, "reliability": 85, "needs_api_key": True},
]


def _engine_instances(headless=True, user_data_dir=""):
    """实例化全部引擎（延迟导入避免循环）。"""
    from .engines import (ScraplingEngine, DirectExtractEngine,
                          BrowserUseEngine, Crawl4AIEngine)
    from .agent_engine import BrowserAgentEngine
    return {
        "scrapling": ScraplingEngine(),
        "direct": DirectExtractEngine(),
        "browser-use": BrowserUseEngine(headless=headless,
                                        user_data_dir=user_data_dir),
        "agent": BrowserAgentEngine(headless=headless,
                                    user_data_dir=user_data_dir),
        "crawl4ai": Crawl4AIEngine(),
    }


# ---------- 质量评分与仲裁 ----------

class Arbiter:
    """仲裁器：多引擎结果选优/融合。"""

    def __init__(self, fields_hint=None):
        self.fields_hint = fields_hint or []

    def score(self, rows, fields=None) -> QualityScore:
        return QualityScore().compute(rows, fields or self.fields_hint)

    def pick_best(self, results: dict) -> tuple:
        """多个引擎结果 → 质量分最高的。

        results: {engine_name: EngineResult}
        返回 (best_name, best_result, scores)
        """
        scores = {}
        best_name, best_result, best_score = None, None, 0
        for name, res in results.items():
            if not res or not res.rows:
                scores[name] = 0
                continue
            q = self.score(res.rows, res.config.get("fields"))
            scores[name] = q.total
            if q.total > best_score:
                best_score = q.total
                best_name, best_result = name, res
        return best_name, best_result, scores

    def fuse_fields(self, results: dict) -> list:
        """字段级融合：不同引擎提取到不同字段时，按行合并取并集。

        例如引擎A提取了[标题,价格]，引擎B提取了[标题,链接]，
        融合后 = [标题,价格,链接]（标题对齐，缺失补空）。
        """
        valid = {n: r for n, r in results.items() if r and r.rows}
        if not valid:
            return []
        # 用行数最多的做基准
        base_name = max(valid, key=lambda n: len(valid[n].rows))
        base = valid[base_name]
        cols = []
        for r in base.rows:
            for c in r.keys():
                if c not in cols:
                    cols.append(c)
        # 收集其他引擎的额外字段
        others = {n: r for n, r in valid.items() if n != base_name}
        if not others:
            return base.rows
        # 按首字段对齐合并
        key_col = cols[0] if cols else None
        if not key_col:
            return base.rows
        extra_cols = []
        for r in list(others.values())[0].rows:
            for c in r.keys():
                if c not in cols and c not in extra_cols:
                    extra_cols.append(c)
        lookup = {}
        for n, r in others.items():
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
                        merged[c] = v
            fused.append(merged)
        return fused


# ---------- 融合引擎主类 ----------

class UnifiedEngine:
    """统一引擎：对外唯一入口。

    用法：
        from app.unified_engine import UnifiedEngine
        engine = UnifiedEngine()
        result = engine.run("提取书籍标题价格", url="...", api_key="...")
    """

    name = "unified"
    label = "融合引擎（六引擎合体）"

    def __init__(self, headless=True, user_data_dir=""):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.analyzer = ScenarioAnalyzer()
        self.planner = PlanBuilder()
        self.arbiter = Arbiter()

    def available(self) -> bool:
        return True

    def _report(self, progress, msg):
        if progress:
            progress(msg)

    def run(self, user_input: str, url: str = "", api_key: str = "",
            max_retries: int = 3, proxy: str = "", progress=None,
            **kwargs) -> "EngineResult":
        """融合执行入口。"""
        from .engines import EngineResult

        if not url:
            raise ScrapeError("融合引擎需要目标网址")

        # 1. 场景分析
        # 优先 LLM（真正泛化），无 API key 或 LLM 失败时降级到关键词规则
        from .llm import _get_api_key
        api_key = _get_api_key(api_key)
        if api_key and not isinstance(self.analyzer, LLMScenarioAnalyzer):
            self.analyzer = LLMScenarioAnalyzer(api_key=api_key)
        elif not api_key and not isinstance(self.analyzer, ScenarioAnalyzer):
            self.analyzer = ScenarioAnalyzer()
        self._report(progress, "🧠 融合引擎：分析场景…")
        prof = self.analyzer.analyze(user_input, url, proxy, progress)
        self._report(progress,
                     f"📋 场景画像：{prof.scenario}（置信度 {prof.confidence}）"
                     + (f"；原因：{'、'.join(prof.reasons)}" if prof.reasons else ""))

        # 2. 生成执行计划 + 注入 UI 传入的 kwargs（深爬参数等）
        plan = self.planner.build(prof)
        for node in plan.nodes:
            for k, v in kwargs.items():
                if k not in node.params:
                    node.params[k] = v
        self._report(progress,
                     f"🗺️ 执行计划：{' → '.join(n.mode + '(' + '+'.join(n.engine_names) + ')' for n in plan.nodes)}")

        # 3. 执行
        engine_insts = _engine_instances(self.headless, self.user_data_dir)
        ctx = SharedContext()

        final_rows = []
        final_engine = ""
        errors = []

        for node in plan.nodes:
            if final_rows:
                break
            self._report(progress, f"▶ 执行节点（{node.mode}）: "
                                   f"{'+'.join(node.engine_names)}")

            if node.mode == "race":
                rows, engine, errs = self._race(
                    engine_insts, node.engine_names, user_input, url,
                    api_key, proxy, progress, ctx, node)
                errors.extend(errs)
                if rows:
                    final_rows, final_engine = rows, engine
            elif node.mode == "pipeline":
                rows, engine, errs = self._pipeline(
                    engine_insts, node.engine_names, user_input, url,
                    api_key, proxy, progress, ctx, node)
                errors.extend(errs)
                if rows:
                    final_rows, final_engine = rows, engine
            elif node.mode == "enhance":
                # 增强模式：已有结果 + 辅助引擎补字段
                rows, engine, errs = self._enhance(
                    engine_insts, node.engine_names, user_input, url,
                    api_key, proxy, progress, ctx, node, final_rows)
                errors.extend(errs)
                if rows:
                    final_rows, final_engine = rows, engine

        # 4. 共享上下文日志
        self._report(progress, ctx.summary())

        if not final_rows:
            raise ScrapeError(
                "融合引擎所有方案均失败："
                + ("；".join(errors[-5:]) if errors else "无可用引擎"))

        # 5. 质量评分
        q = self.arbiter.score(final_rows)
        self._report(progress,
                     f"📊 质量评分：{q.total}/100"
                     f"（{len(final_rows)}行，字段完整率"
                     f" {round(q.field_completeness*100)}%，"
                     f"去重率 {round(q.unique_rate*100)}%）")

        return EngineResult(rows=final_rows, status=0,
                            used_fetcher=final_engine,
                            engine=self.name, attempts=1)

    # ---------- 三种执行模式 ----------

    def _race(self, insts, names, user_input, url, api_key, proxy,
              progress, ctx, node) -> tuple:
        """并行竞争：多引擎同时跑，先出高分结果者胜。"""
        results = {}
        errors = []
        lock = threading.Lock()
        winner = {"name": None, "score": 0}

        def worker(eng_name):
            eng = insts.get(eng_name)
            if not eng or not eng.available():
                return
            try:
                params = dict(node.params)
                import inspect
                sig = inspect.signature(eng.run)
                eng_kwargs = {k: v for k, v in params.items()
                              if k in sig.parameters}
                res = eng.run(user_input, url, api_key, max_retries=1,
                              proxy=proxy, progress=progress, **eng_kwargs)
                if res and res.rows:
                    q = self.arbiter.score(res.rows,
                                           res.config.get("fields"))
                    with lock:
                        results[eng_name] = res
                        if q.total > winner["score"]:
                            winner["name"] = eng_name
                            winner["score"] = q.total
            except Exception as e:
                with lock:
                    errors.append(f"{eng_name}: {str(e)[:100]}")

        threads = [threading.Thread(target=worker, args=(n,), daemon=True)
                   for n in names if insts.get(n)]
        start = time.time()
        for t in threads:
            t.start()
        # 轮询等待：任一出结果且质量达标即收（或超时）
        # 阈值分级：有 1 行以上且字段非全空就算"可用"（score>=15），
        # 高分（>=50）立即收；避免因 AI 余额不足等导致的有效结果被误杀
        while time.time() - start < node.timeout:
            time.sleep(1)
            with lock:
                if winner["name"]:
                    if winner["score"] >= 50:
                        break
                    # 所有引擎都已结束（不再有新结果）→ 用当前最佳
                    if not any(t.is_alive() for t in threads) \
                            and winner["score"] >= 15:
                        break
            if not any(t.is_alive() for t in threads):
                break
        # 收尾：等还在跑的线程结束（最多再等 5 秒）
        for t in threads:
            t.join(timeout=5)

        # 最终：有结果且非全空即采用（不再卡 40 分硬门槛）
        if winner["name"] and winner["score"] >= 15:
            best = results.get(winner["name"])
            return best.rows, winner["name"], errors
        return [], "", errors

    def _pipeline(self, insts, names, user_input, url, api_key, proxy,
                  progress, ctx, node) -> tuple:
        """管道协作：第一个引擎产出中间产物 → 后续引擎消费。

        典型：agent 引擎登录/渲染后把 DOM/HTML 快照放进 ctx，
        后续提取引擎（scrapling/crawl4ai）消费共享上下文。
        """
        results = {}
        errors = []
        for i, eng_name in enumerate(names):
            eng = insts.get(eng_name)
            if not eng or not eng.available():
                continue
            try:
                self._report(progress, f"  ↳ 管道环节 {i+1}/{len(names)}: {eng.label}")
                import inspect
                sig = inspect.signature(eng.run)
                eng_kwargs = {k: v for k, v in node.params.items()
                              if k in sig.parameters}
                res = eng.run(user_input, url, api_key, max_retries=1,
                              proxy=proxy, progress=progress, **eng_kwargs)
                if res and res.rows:
                    results[eng_name] = res
                    # 管道成功后，把产物共享给后续（提取引擎可直接用）
                    return res.rows, eng_name, errors
            except Exception as e:
                errors.append(f"{eng_name}: {str(e)[:100]}")
        return [], "", errors

    def _enhance(self, insts, names, user_input, url, api_key, proxy,
                 progress, ctx, node, base_rows) -> tuple:
        """增强模式：主结果 + 辅助引擎补字段（字段级融合）。"""
        if not base_rows:
            return [], "", []
        results = {"base": type("R", (), {"rows": base_rows,
                                          "config": {}})()}
        errors = []
        for eng_name in names:
            eng = insts.get(eng_name)
            if not eng or not eng.available():
                continue
            try:
                res = eng.run(user_input, url, api_key, max_retries=1,
                              proxy=proxy, progress=progress)
                if res and res.rows:
                    results[eng_name] = res
            except Exception as e:
                errors.append(f"{eng_name}: {str(e)[:100]}")
        fused = self.arbiter.fuse_fields(results)
        return fused, "+".join(results.keys()), errors
