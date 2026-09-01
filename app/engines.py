# -*- coding: utf-8 -*-
"""多引擎爬虫框架：引擎抽象 + 调度器。

引擎清单（按优先级）：
    1. ScraplingEngine   —— selector 模式（L1 真实页面分析 + 智能 fallback + 翻页）
    2. DirectExtractEngine —— AI 直提（渲染后全文 → LLM 直接提取字段，兜底）
    3. BrowserUseEngine  —— 反爬引擎（模拟真人操作，安装 browser-use 后启用）
    4. Crawl4AIEngine    —— 深爬引擎（整站 BFS/DFS，安装 crawl4ai 后启用）

调度器 run_pipeline 按顺序尝试可用引擎，第一个成功即返回。
"""
from dataclasses import dataclass, field

from .scraper import ScrapeError, fetch_page, auto_fetch
from .llm import generate_config, fix_config, direct_extract


def _absolutize_links(rows: list, base_url: str) -> list:
    """模块级：把行里的相对链接/图片路径拼成完整 URL（基于抓取页 base_url）。

    对所有引擎结果（agent/scrapling/crawl4ai 等）统一过一道，确保前端拿到的
    都是完整可点击的 URL。例：
      /15_15556/        → https://www.biquga.com/15_15556/
      /img/15556.jpg    → https://www.biquga.com/img/15556.jpg
      ../catalogue/x.html → https://.../catalogue/x.html
    """
    if not rows or not base_url:
        return rows
    from urllib.parse import urljoin, urlparse
    base = base_url if base_url.startswith(("http://", "https://")) \
        else "https://" + base_url
    scheme = urlparse(base).scheme
    host = urlparse(base).netloc
    base_full = f"{scheme}://{host}" if scheme and host else base

    out = []
    for row in rows:
        row = dict(row)
        for k, v in list(row.items()):
            if not isinstance(v, str) or not v:
                continue
            is_url_field = any(kw in k.lower() for kw in (
                "链接", "url", "网址", "href", "图片", "封面",
                "缩略图", "img", "image", "cover", "src",
                "link", "地址"))
            starts_path = v.startswith(("/", "../", "./"))
            if (is_url_field or starts_path) and starts_path:
                if v.startswith("/"):
                    row[k] = base_full + v
                else:
                    row[k] = urljoin(base_full + "/", v)
        out.append(row)
    return out


@dataclass
class EngineResult:
    """引擎统一输出。"""
    rows: list = field(default_factory=list)
    status: int = 0
    used_fetcher: str = ""
    engine: str = ""          # 引擎名（scrapling / direct / browser-use / crawl4ai）
    config: dict = field(default_factory=dict)
    attempts: int = 0


class BaseEngine:
    """引擎基类：统一接口。"""
    name = "base"
    label = "基础引擎"

    def available(self) -> bool:
        """引擎是否可用（依赖是否安装）。"""
        return True

    def run(self, user_input: str, url: str = "", api_key: str = "",
            max_retries: int = 3, proxy: str = "",
            progress: callable = None) -> EngineResult:
        raise NotImplementedError


# ---------- 公共辅助 ----------

def _report(progress, msg):
    if progress:
        progress(msg)


def _is_login_wall_text(text: str) -> bool:
    """判断页面文本是否命中登录墙/滑块/验证码特征（browser-use 引擎用）。"""
    t = (text or "").lower()
    markers = ["登录", "扫码", "二维码", "请输入验证码", "人机验证",
               "滑块", "请完成验证", "sign in", "login", "captcha",
               "qr code"]
    return sum(1 for m in markers if m in t) >= 2


def _run_async(coro_factory):
    """跨事件循环运行异步协程（解决 flet UI 与 asyncio 的嵌套 loop 冲突）。

    - 当前线程没有运行中的 loop（后台工作线程）→ asyncio.run 正常创建新 loop
    - 当前线程已有运行中的 loop（主线程/UI 线程直接调用）→ 用 nest_asyncio
      补丁后在原 loop 上运行，避免 "event loop is already running" 报错
    """
    import asyncio
    import warnings
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的 loop：最常规路径（BrowserUseEngine 在后台线程被调用）
        return asyncio.run(coro_factory())
    # 当前线程已有运行中的 loop：需要 nest_asyncio 桥接
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        raise ScrapeError(
            "当前线程已存在事件循环，需安装 nest_asyncio 才能运行"
            "（pip install nest_asyncio）") from None
    with warnings.catch_warnings():
        # Python 3.12+ 中 ensure_future 的 loop 参数已弃用，
        # 统一改用 loop.create_task 避免 DeprecationWarning
        warnings.simplefilter("ignore", DeprecationWarning)
        task = loop.create_task(coro_factory())
    return loop.run_until_complete(task)


def validate(rows: list, fields: list) -> tuple:
    """校验抓取结果是否合理。返回 (ok, message)。"""
    if not rows:
        return False, "没有提取到任何数据，页面结构可能不匹配"
    for f in fields:
        name = f.get("name", "")
        column = [r.get(name, "") for r in rows]
        if not any(str(v).strip() for v in column):
            return False, f"字段「{name}」没有任何内容，选择器可能错误"
    return True, ""


def _get_initial_snippet(url: str, proxy: str = "") -> str:
    """L1：AI 生成配置前先看真实页面结构（静态优先，内容太少换浏览器渲染）。

    返回页面片段 + 真实 DOM 元素清单（让 AI 看 tag/class/文本生成精准 selector，不再盲猜）。
    """
    if not url:
        return ""
    for fetcher, wait in (("static", 2), ("stealthy", 4)):
        try:
            page = fetch_page(url, fetcher, wait, proxy)
            body = getattr(page, "body", "") or ""
            if isinstance(body, bytes):
                body = body.decode("utf-8", "ignore")
            if len(body.strip()) > 300:
                # 提取真实元素清单（让 AI 看 tag.class 而非凭空猜 selector）
                try:
                    from .element_summary import extract_element_summary
                    elements = extract_element_summary(body)
                except Exception:
                    elements = ""
                snippet = body[:2500]
                if elements:
                    return (f"{snippet}\n\n"
                            f"[真实页面元素清单，写 selector 时参考不要凭空猜]\n"
                            f"{elements}")
                return snippet
        except Exception:
            continue
    return ""


def _page_snippet(url: str, config: dict, wait_seconds: int = 2,
                  proxy: str = "") -> str:
    """获取页面 HTML 片段（供 AI 修正选择器时参考）。"""
    fetcher = config.get("fetcher", "auto")
    if fetcher == "auto":
        fetcher = "dynamic"
    for f in (fetcher, "static"):
        try:
            page = fetch_page(url, f, wait_seconds, proxy)
            body = getattr(page, "body", "") or ""
            if body.strip():
                return body[:2500]
        except Exception:
            continue
    return ""


def _page_text(url: str, wait_seconds: int = 4, proxy: str = "") -> str:
    """获取页面渲染后文本（先尝试去噪正文提取，退回落 markdown）。"""
    page = fetch_page(url, "stealthy", wait_seconds, proxy)
    body = getattr(page, "body", "") or ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "ignore")
    # 优先：内容去噪提取正文（借鉴 Crawl4AI）
    try:
        from .content_filter import extract_clean_text
        clean = extract_clean_text(body)
        if len(clean.strip()) > 100:
            return clean
    except Exception:
        pass
    # 退路：markdown
    md_fn = getattr(page, "markdown", None)
    try:
        text = md_fn() if callable(md_fn) else body
    except Exception:
        text = body
    return text or body


# ---------- 引擎1：Scrapling（selector 模式） ----------

class ScraplingEngine(BaseEngine):
    name = "scrapling"
    label = "selector 引擎"

    def run(self, user_input, url="", api_key="", max_retries=3,
            proxy="", progress=None) -> EngineResult:
        _report(progress, "① 正在打开目标网页，分析页面结构…")
        snippet = _get_initial_snippet(url, proxy)
        _report(progress, "② 正在调用 AI 生成爬取配置…")
        config = generate_config(user_input, url, api_key,
                                 page_snippet=snippet)
        target_url = config["url"] or url
        if not target_url:
            raise ScrapeError("AI 未能确定目标网址，请手动填写 URL")

        pagination = config.get("pagination") or {"mode": "none"}
        wait = config.get("wait_seconds", 2)
        _report(progress, f"AI 配置完成（字段 {len(config['fields'])} 个，"
                          f"抓取器 {config.get('fetcher', 'auto')}，"
                          f"翻页 {pagination.get('mode', 'none')}）")

        last_msg = ""
        for attempt in range(1, max_retries + 1):
            _report(progress, f"③ 正在抓取数据（第 {attempt} 次尝试）…")
            try:
                rows, status, used = auto_fetch(
                    target_url, config["fields"], wait, proxy, pagination)
                ok, msg = validate(rows, config["fields"])
                if ok:
                    _report(progress, f"✅ 抓取完成：{used} 模式，共 {len(rows)} 行数据")
                    return EngineResult(rows=rows, status=status,
                                        used_fetcher=used, engine=self.name,
                                        config=config, attempts=attempt)
                last_msg = msg
                _report(progress, f"⚠️ {msg}，正在让 AI 修正…")
            except ScrapeError as e:
                last_msg = str(e)
                rows = []
                used = "auto"
                _report(progress, f"⚠️ 抓取失败：{e}，正在让 AI 修正…")

            _report(progress, "④ 正在获取页面细节并调用 AI 修正配置…")
            snippet = _page_snippet(target_url, config, wait, proxy)
            try:
                config = fix_config(user_input, config, last_msg, snippet,
                                    api_key)
                new_url = config.get("url") or target_url
                if new_url != target_url:
                    target_url = new_url
            except Exception as e:
                raise ScrapeError(f"AI 修正配置失败：{e}") from e

        raise ScrapeError(f"selector 引擎尝试 {max_retries} 次仍失败：{last_msg}")


# ---------- 引擎2：AI 直提（兜底） ----------

class DirectExtractEngine(BaseEngine):
    name = "direct"
    label = "AI 直提引擎"

    def run(self, user_input, url="", api_key="", max_retries=3,
            proxy="", progress=None) -> EngineResult:
        _report(progress, "⑤ 切换 AI 直提模式：直接分析页面内容提取数据…")
        if not url:
            raise ScrapeError("AI 直提需要目标网址")
        text = _page_text(url, proxy=proxy)
        rows = direct_extract(user_input, text, api_key)
        _report(progress, f"✅ AI 直提完成：共 {len(rows)} 行数据")
        return EngineResult(rows=rows, status=0, used_fetcher="直提",
                            engine=self.name, attempts=1)


# ---------- 引擎3：browser-use（AI 操作浏览器，官方库） ----------

class BrowserUseEngine(BaseEngine):
    name = "browser-use"
    label = "browser-use 引擎（AI 操作浏览器）"

    def __init__(self, headless: bool = True, user_data_dir: str = ""):
        self.headless = headless
        self.user_data_dir = user_data_dir

    def available(self) -> bool:
        try:
            from browser_use.agent.service import Agent  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self, user_input, url="", api_key="", max_retries=3,
            proxy="", progress=None) -> EngineResult:
        import asyncio
        import json
        import re

        from browser_use.agent.service import Agent
        from browser_use.browser.session import BrowserSession
        from langchain_openai import ChatOpenAI

        from .llm import _get_api_key

        key = _get_api_key(api_key)
        if not key:
            raise ScrapeError("未配置 API Key")
        if not url:
            raise ScrapeError("browser-use 引擎需要目标网址")
        _report(progress, "⑥ 启动 browser-use 引擎（AI 操作浏览器）…")

        task = (
            f"打开网页 {url}。\n"
            f"任务：{user_input}（从页面中提取这些数据）。\n"
            "步骤：先观察页面内容，找到重复的数据条目（列表/卡片），"
            "然后提取每条的名称、价格/详情、链接等字段。\n"
            "最后输出一个 JSON 数组，格式 [{\"字段名\":\"值\"},...]，"
            "字段名用中文。只输出 JSON 数组本身。"
        )

        def _make_llm():
            # ⚠️ browser-use Agent 内部可能从环境变量 OPENAI_API_KEY 读 key，
            # 保险起见也设上（与 litellm 同问题）。langchain-openai 的
            # ChatOpenAI(api_key=key) 显式传参应优先于环境变量。
            import os
            os.environ["OPENAI_API_KEY"] = key
            os.environ["DEEPSEEK_API_KEY"] = key
            # browser-use 0.13 读取 llm.provider / llm.model（langchain-openai
            # 某些版本没有），给类补上类属性
            for attr, val in (("provider", "deepseek"), ("model", None)):
                try:
                    if not hasattr(ChatOpenAI, attr):
                        setattr(ChatOpenAI, attr, val)
                except Exception:
                    pass
            llm = ChatOpenAI(
                model="deepseek-chat",
                api_key=key,
                base_url="https://api.deepseek.com",
            )
            # pydantic 模型拒绝常规赋值，用 object.__setattr__ 强制写入
            try:
                object.__setattr__(llm, "model", "deepseek-chat")
            except Exception:
                pass
            return llm

        async def _run_agent():
            """改造点①：浏览器会话与 agent 分离，先弹窗等登录再让 AI 干活。

            之前浏览器生命周期 = agent.run 生命周期，AI 一决策 done 就关浏览器，
            用户根本没时间登录。现在（参照官方源码注释推荐用法）：
              1. BrowserSession 直接传参启动（官方推荐：不包 BrowserProfile，
                 headless/user_data_dir/keep_alive 均可直接传）
              2. 先导航到目标页（登录/验证需要真实页面）
              3. 有头模式：等用户登录/滑块，最多 30 秒（页面出现内容即提前继续）
              4. Agent 复用同一个已登录会话，登录态不丢失
              5. enable_signal_handler=False：官方 SignalHandler 默认注册
                 SIGINT/SIGTERM 处理，嵌入 flet 应用时应禁用（官方注释：
                 "Useful when embedding browser-use in applications that
                 manage their own signals"），避免抢主程序的 Ctrl+C
            """
            session = BrowserSession(
                headless=self.headless,
                user_data_dir=self.user_data_dir or None,
            )
            await session.start()
            try:
                # 先打开目标页，让用户能看到要登录/验证的页面
                try:
                    await session.navigate_to(url)
                except Exception as e:
                    _report(progress, f"⚠️ 首次打开页面异常：{e}，继续…")
                await asyncio.sleep(2)

                # 有头模式：等待用户人工登录/滑块（最多 30 秒）
                if not self.headless:
                    _report(progress, "🌐 浏览器已打开！如需登录/滑块验证，"
                                      "请在弹出的窗口中手动完成；完成后自动"
                                      "继续爬取（等待 30 秒；登录态会保存，"
                                      "下次自动恢复）…")
                    waited = 0
                    while waited < 30:
                        await asyncio.sleep(3)
                        waited += 3
                        # 读取当前页面文本判断登录墙是否已解除
                        body_text = ""
                        try:
                            page = await session.must_get_current_page()
                            body_text = await page.evaluate(
                                "() => (document.body.innerText || '').slice(0, 2000)")
                        except Exception:
                            pass
                        if _is_login_wall_text(body_text):
                            _report(progress, f"⏳ 检测到登录/验证，继续等待您操作…"
                                              f"（已等待 {waited} 秒）")
                        else:
                            _report(progress, f"✅ 登录/验证完成"
                                              f"（等待 {waited} 秒），开始爬取…")
                            break
                    else:
                        _report(progress, "⏰ 30 秒等待结束，开始爬取"
                                          "（登录态已保存，下次自动恢复）…")

                # Agent 复用同一个已登录会话（登录态/滑块结果不丢失）
                agent = Agent(
                    task=task,
                    llm=_make_llm(),
                    browser_session=session,
                    enable_signal_handler=False,  # 嵌入 flet，禁用自己的信号处理
                )
                # 兜底总超时（15 步 × 每步最多 60 秒），避免永久挂起
                history = await asyncio.wait_for(
                    agent.run(max_steps=15), timeout=900)
                return history.final_result() if history else ""
            finally:
                try:
                    await session.kill()
                except Exception:
                    pass

        try:
            raw = _run_async(_run_agent)
        except Exception as e:
            raise ScrapeError(f"browser-use 执行失败：{e}") from e

        if not raw:
            raise ScrapeError("browser-use 未返回结果")
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            raise ScrapeError(f"browser-use 输出不是 JSON 数组：{raw[:120]}")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ScrapeError(f"browser-use 输出解析失败：{e}") from e
        rows = [d for d in data if isinstance(d, dict)]
        if not rows:
            raise ScrapeError("browser-use 未提取到数据")
        _report(progress, f"✅ browser-use 完成：{len(rows)} 行")
        return EngineResult(rows=rows, status=0, used_fetcher="browser-use",
                            engine=self.name, attempts=1)


# ---------- 引擎4：Crawl4AI（官方库完整接入） ----------

class Crawl4AIEngine(BaseEngine):
    """Crawl4AI 官方库完整接入（third_party/crawl4ai-main / pip crawl4ai 0.9.3）。

    覆盖官方全部能力（与官方文档一致，无取舍）：
    - AsyncWebCrawler 基础抓取（headless Chromium，markdown 输出）
    - JsonCssExtractionStrategy：CSS/XPath 结构化提取（schema: baseSelector + fields）
    - LLMExtractionStrategy：LLM 提取（schema / 自由指令）
    - BFSDeepCrawlStrategy / DFSDeepCrawlStrategy：整站深爬
    - PruningContentFilter / BM25ContentFilter：内容去噪
    - CacheMode：缓存控制
    """

    name = "crawl4ai"
    label = "Crawl4AI 引擎（官方）"

    def available(self) -> bool:
        try:
            import crawl4ai  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self, user_input, url="", api_key="", max_retries=3,
            proxy="", progress=None, deep_max_depth: int = 2,
            max_pages: int = 10, cache_mode: str = "bypass",
            use_llm_extraction: bool = False) -> EngineResult:
        """运行 Crawl4AI 引擎。

        参数：
            deep_max_depth: 深爬 BFS 最大深度（默认 2，0 表示仅首页）
            max_pages: 深爬最大页数限制
            cache_mode: bypass=每次新抓 / enabled=启用缓存
            use_llm_extraction: True 用 LLMExtractionStrategy（需 api_key），
                                False 用 JsonCssExtractionStrategy（无 LLM 成本）
        """
        import asyncio
        import json

        from crawl4ai import (AsyncWebCrawler, BrowserConfig,
                              CrawlerRunConfig, CacheMode)
        from crawl4ai.extraction_strategy import (JsonCssExtractionStrategy,
                                                  LLMExtractionStrategy)
        from crawl4ai.content_filter_strategy import PruningContentFilter

        if not url:
            raise ScrapeError("Crawl4AI 引擎需要目标网址")

        _report(progress, "⑦ 启动 Crawl4AI 引擎（官方库）…")

        # 字段需求解析：优先用 AI 生成 schema（官方 generate_schema 思路，
        # 一次 LLM 成本换可复用精确 schema），失败则用通用 schema
        fields = self._parse_fields(user_input)

        async def _run():
            browser_conf = BrowserConfig(headless=True)
            cache = (CacheMode.ENABLED if cache_mode == "enabled"
                     else CacheMode.BYPASS)

            if use_llm_extraction and api_key:
                # LLM 提取（官方 LLMExtractionStrategy）
                from crawl4ai import LLMConfig
                strategy = None
                try:
                    from .llm import _get_api_key
                    key = _get_api_key(api_key)
                    if key:
                        llm_config = LLMConfig(
                            provider="deepseek/deepseek-chat",
                            api_token=key,
                            base_url="https://api.deepseek.com",
                        )
                        strategy = LLMExtractionStrategy(
                            llm_config=llm_config,
                            instruction=f"从页面提取：{user_input}",
                            extraction_type="schema",
                            schema={"type": "object",
                                    "properties": {f["name"]: {
                                        "type": "string"}
                                        for f in fields}},
                            verbose=True,
                        )
                except Exception as e:
                    _report(progress, f"⚠️ LLM 配置失败，回退 CSS 提取：{e}")
                    strategy = None
            else:
                strategy = None

            if strategy is None:
                # CSS/XPath 结构化提取（官方 JsonCssExtractionStrategy）
                schema = None
                # 优先：官方 generate_schema 让 LLM 从真实页面生成精确 schema
                # （等价于 AI 打开开发者工具看源代码找字段，一次成本换可复用）
                try:
                    from .llm import _get_api_key
                    import os
                    key = _get_api_key(api_key)
                    if key:
                        # ⚠️ 关键：litellm 默认从环境变量 DEEPSEEK_API_KEY 读 key，
                        # 忽略 LLMConfig.api_token！必须显式设置环境变量，
                        # 否则会用旧/空的 key 导致 402 Insufficient Balance。
                        os.environ["DEEPSEEK_API_KEY"] = key
                        os.environ["OPENAI_API_KEY"] = key
                        from crawl4ai import LLMConfig
                        llm_config = LLMConfig(
                            provider="deepseek/deepseek-chat",
                            api_token=key,
                            base_url="https://api.deepseek.com",
                        )
                        _report(progress, "🔍 正在让 AI 分析页面结构生成精确提取规则…")
                        schema = JsonCssExtractionStrategy.generate_schema(
                            url=url,
                            query=user_input,
                            llm_config=llm_config,
                            validate=True,
                            max_refinements=1,
                        )
                        if schema:
                            _report(progress, "✅ AI 已生成精确提取规则")
                except Exception as e:
                    _report(progress, f"⚠️ AI 生成规则失败，使用通用规则：{e}")
                    schema = None
                if not schema:
                    schema = self._build_schema(fields)
                strategy = JsonCssExtractionStrategy(schema)

            run_conf = CrawlerRunConfig(
                cache_mode=cache,
                extraction_strategy=strategy,
            )

            # 深爬：BFSDeepCrawlStrategy（官方 deep_crawling 模块）
            # 触发场景：①明确指向多页面/整站 ②"前N页/翻N页/N页"等多页指令
            # 触发场景：①用户 UI 填了 pg_deep（deep_max_depth>0）
            #          ②指令含"前N页/翻N页/整站"等多页词（即使 UI 没填也自动深爬）
            deep_keywords = (
                "整站", "全站", "所有页面", "全部页面",
                "整个网站", "所有分类", "全部分类",
                "所有链接", "全部链接", "所有文章",
                "所有新闻", "所有帖子", "所有栏目",
                "全部栏目", "网站所有", "爬取整个",
                "遍历全站", "全站所有", "前十页", "前N页",
                "前几页", "多页", "翻页", "翻到第")
            is_deep = any(k in user_input for k in deep_keywords) or (deep_max_depth and deep_max_depth > 0)
            # 指令含深爬词但 UI 没填 pg_deep → 自动给默认 depth 1
            _effective_depth = deep_max_depth if deep_max_depth else 0
            # 从指令自动提取"前N页/N页/翻N页"里的数字（默认 10 页）
            # 注意：用 _extracted_max 而不直接改 max_pages（避免 UnboundLocalError——
            # Python 看到 if-block 里有 max_pages=... 赋值就把整个变量当 local）
            _extracted_max = max_pages
            if is_deep and max_pages == 10:
                import re
                m = re.search(r"前\s*(\d+)\s*页", user_input or "")
                if m:
                    _extracted_max = int(m.group(1))
                else:
                    m2 = re.search(r"(?:翻|爬|采)\s*(\d+)\s*页", user_input or "")
                    if m2:
                        _extracted_max = int(m2.group(1))
            if is_deep and _effective_depth == 0:
                _effective_depth = 1
                # BFS 会沿分页链接爬，也会进入详情页消耗 pages 预算；
                # 至少 50 个 URL 预算确保能爬多个分类页
                _extracted_max = max(_extracted_max, 50)
                _report(progress, f"🕸️ 检测到多页指令，自动启用 BFS 深爬"
                                 f"（max_depth=1, max_pages={_extracted_max}）")
            if is_deep:
                try:
                    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
                    run_conf = CrawlerRunConfig(
                        cache_mode=cache,
                        extraction_strategy=strategy,
                        deep_crawl_strategy=BFSDeepCrawlStrategy(
                            max_depth=_effective_depth,
                            include_external=False,
                            max_pages=_extracted_max,
                        ),
                    )
                except Exception as e:
                    _report(progress, f"⚠️ 深爬策略配置失败，降级单页：{e}")

            async with AsyncWebCrawler(config=browser_conf) as crawler:
                result = await crawler.arun(url=url, config=run_conf)
                return result

        result = _run_async(_run)

        # 解析结果（支持单页 CrawlResult 和深爬 list 两种返回）
        rows = []

        def _collect(content):
            """从 extracted_content JSON 收集行。"""
            out = []
            if not content:
                return out
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    out.extend(d for d in data if isinstance(d, dict))
                elif isinstance(data, dict):
                    out.append(data)
            except (json.JSONDecodeError, TypeError):
                pass
            return out

        if isinstance(result, list):
            # 深爬：多页面结果合并 + 按首字段去重（跨页重复内容）
            seen = set()
            for r in result:
                for row in _collect(getattr(r, "extracted_content", "")):
                    key = str(row.get(next(iter(row), ""), ""))
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    rows.append(row)

            # 字段空率检测：AI 推断的 schema 在笔趣阁等"链接+标题"结构上
            # 可能 baseSelector 选错（如选了 .item 但页面用 <ul><li>），
            # 导致行数多但字段全空。BFS 深爬后检测：若 >30% 行核心字段为空
            # → 自动 fallback 用更宽泛的 schema 重跑
            if rows and self._has_too_many_empty_fields(rows):
                _report(progress,
                        "⚠️ AI schema 字段空率过高，自动 fallback 到宽泛 schema 重跑…")
                fallback_rows = _run_async(self._fallback_deep_crawl(
                    url, user_input, fields, deep_max_depth, max_pages,
                    cache, browser_conf, progress))
                if fallback_rows:
                    # 用 fallback 覆盖原结果
                    rows = fallback_rows
        else:
            rows = _collect(getattr(result, "extracted_content", ""))

        if not rows:
            raise ScrapeError(
                f"Crawl4AI 未提取到结构化数据（页面可能无匹配元素或加载失败）。"
                f"原始输出长度: {len(getattr(result, 'extracted_content', '') or '')}")

        # 链接/图片绝对化：相对路径（/15_15556/ 或 /img/x.jpg）→ 完整 URL
        # （用户要求"链接可点击、图片可点击显示"；前端用完整 URL 才能渲染超链接）
        rows = self._absolutize_links(rows, url)

        _report(progress, f"✅ Crawl4AI 完成：{len(rows)} 行")
        return EngineResult(rows=rows, status=0, used_fetcher="crawl4ai",
                            engine=self.name, attempts=1)

    def _absolutize_links(self, rows: list, base_url: str) -> list:
        return _absolutize_links(rows, base_url)

    def _parse_fields(self, user_input: str) -> list:
        """从自然语言解析字段（支持"提取X、Y和Z"/"提取XY"等格式）。"""
        import re
        # 常见字段名词典（匹配"提取书籍标题价格"这类无分隔写法）
        FIELD_DICT = [
            ("标题", ("标题", "书名", "题目", "名称", "标题名")),
            ("价格", ("价格", "价钱", "售价", "金额")),
            ("链接", ("链接", "网址", "url", "URL", "地址")),
            ("图片", ("图片", "图片链接", "封面", "缩略图", "img")),
            ("作者", ("作者", "作家")),
            ("内容", ("内容", "描述", "简介", "详情", "摘要")),
            ("时间", ("时间", "日期", "发布时间")),
            ("评分", ("评分", "评分值", "星级")),
        ]
        matched = []
        for disp, kws in FIELD_DICT:
            for kw in kws:
                if kw in user_input:
                    matched.append(disp)
                    break
        if matched:
            return [{"name": n, "selector": "", "type": "text"}
                    for n in matched[:10]]
        # 尝试「提取...的X、Y和Z」
        m = re.search(r"(?:提取|爬取|采集)[^，。]*?(?:的|：|:)(.+)$", user_input)
        if m:
            names = re.split(r"[、，,和及/]+", m.group(1).strip())
            names = [n.strip() for n in names if n.strip()][:10]
            if names:
                return [{"name": n, "selector": "", "type": "text"}
                        for n in names]
        # 默认字段：带封面/简介/链接（小说/书籍/商品类站点常见）
        return [
            {"name": "标题", "selector": "h1, h2, h3, .title, dt a", "type": "text"},
            {"name": "作者", "selector": ".author, [class*=author], dt span:first-child",
             "type": "text"},
            {"name": "封面", "selector": ".image img, .cover img, img[src*='/img/']",
             "type": "image"},
            {"name": "简介", "selector": "dd, .desc, [class*=desc], p.desc", "type": "text"},
            {"name": "链接", "selector": "a[href*='_'], a", "type": "attr", "attr": "href"},
        ]

    def _build_schema(self, fields: list) -> dict:
        """把字段列表转成 JsonCssExtractionStrategy 的 schema。

        智能生成：字段名 → 常见 class/标签推断（标题/价格/链接/图片等），
        baseSelector 用多候选（列表容器常见结构）。
        """
        # 字段名 → 常见 selector 推断（带笔趣阁等小说站专用 selector）
        FIELD_SELECTOR_HINTS = [
            ("标题", "h2, h3, h4, .title, [class*=title], [class*=name], "
                    "a[title], dt a"),
            ("价格", ".price, .price_color, [class*=price], [class*=cost], "
                    "[class*=amount], [class*=rmb]"),
            ("链接", "a[href*='_'], dt a, a"),
            ("图片", ".image img, .cover img, img[src*='/img/']"),
            ("封面", ".image img, .cover img, img[src*='/img/']"),
            ("作者", "dt span:first-child, .author, [class*=author], "
                    "[class*=writer]"),
            ("简介", "dd, .desc, [class*=desc], [class*=intro], "
                    "[class*=summary]"),
            ("描述", "dd, .desc, [class*=desc], [class*=intro]"),
            ("内容", "p, dd, .desc, [class*=desc], [class*=content], "
                    "[class*=intro], [class*=summary]"),
            ("时间", "time, .time, [class*=date], [class*=time]"),
        ]
        # 字段名 → 类型推断（关键：封面类→image，链接类→attr）
        def _infer_type(name, default="text"):
            n = name or ""
            if any(k in n for k in ("封面", "图片", "img", "image", "cover", "缩略图", "海报")):
                return "image"
            if any(k in n for k in ("链接", "网址", "url", "URL", "地址", "href")):
                return "attr"
            return default
        schema_fields = []
        for f in fields:
            name = f.get("name", "字段")
            sel = f.get("selector") or "h3, h2, p"
            # 字段名匹配常见模式
            if not f.get("selector"):
                for kw, hint in FIELD_SELECTOR_HINTS:
                    if kw in name:
                        sel = hint
                        break
            # 智能类型推断（如果用户没显式指定 type）
            ftype = f.get("type") or _infer_type(name)
            schema_fields.append({
                "name": name,
                "selector": sel,
                "type": ("attribute" if ftype == "attr" else
                         "image" if ftype == "image" else "text"),
                "attribute": (f.get("attr", "href") if ftype == "attr"
                              else "src" if ftype == "image" else None),
            })
        return {
            "name": "页面数据",
            # baseSelector 加 .txt-list li / a[href*='_'] 等笔趣阁/小说站常见结构
            "baseSelector": ("article, .product, .product_pod, .item, "
                             ".card, .goods, .txt-list li, .book-list li, "
                             ".book, ul.list li, [class*=product], "
                             "[class*=item], [class*=card], [class*=book], li"),
            "fields": schema_fields,
        }

    def _has_too_many_empty_fields(self, rows: list, threshold: float = 0.3) -> bool:
        """检测 BFS 深爬结果是否大量行核心字段为空（AI schema 选错的信号）。

        阈值：超过 30% 的行核心字段（第一个非空键的字段）为空 → 触发 fallback。
        """
        if not rows:
            return False
        # 取第一个有数据的行的第一个字段（AI 推断的"主字段"如 book_name）
        for r in rows:
            keys = [k for k in r.keys() if k]
            if keys:
                main_field = keys[0]
                break
        else:
            return False
        empty_count = sum(1 for r in rows
                          if not str(r.get(main_field, "")).strip())
        return (empty_count / len(rows)) > threshold

    def _fallback_deep_crawl(self, url, user_input, fields, deep_max_depth,
                             max_pages, cache, browser_conf, progress):
        """深爬 fallback：BFS 跑完后字段空率高时，用最宽泛 schema 重新跑 BFS。

        思路：AI 推断的 schema 在笔趣阁等"链接直接当标题"的站点上选错
        baseSelector；用最宽泛的 selector（a[href*='_'] 小说站链接格式）重跑。
        """
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
        from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

        # 字段名推断（按用户指令中常见中文名）
        # 直接从 fields 拿，没 fields 时用通用名
        field_names = [f.get("name", "字段") for f in (fields or [])] \
                      or ["书名", "作者", "链接"]
        # 映射到 selector 提示：笔趣阁等小说站结构：<dt><span>作者</span>
        # <a>书名</a></dt>，分页/详情链接是 <a href*='_'>，封面是 .image img
        FIELD_SELECTORS = {
            "标题": "dt a, h3 a, .title a, a[href*='_']",
            "书名": "dt a, h3 a, .title a, a[href*='_']",
            "小说名": "dt a, h3 a, .title a, a[href*='_']",
            "文章": "dt a, h3 a, .title a, a[href*='_']",
            "商品": "dt a, h3 a, .title a, a[href*='_']",
            "作者": "dt span:first-child, .author, span:first-child",
            "作者名": "dt span:first-child, .author, span:first-child",
            "作者名称": "dt span:first-child, .author, span:first-child",
            "链接": "a[href*='_']",
            "小说链接": "a[href*='_']",
            "URL": "a[href*='_']",
            "url": "a[href*='_']",
            "封面": ".image img, img",
            "图片": ".image img, img",
            "最新章节": ".chapter, dd, .lastest, [class*=chapter] a",
        }

        def _infer_type(name):
            """根据字段名推断 Crawl4AI 字段类型。"""
            n = name or ""
            if any(k in n for k in ("封面", "图片", "img", "image",
                                   "cover", "缩略图", "海报", "插图")):
                return ("image", "src")
            if any(k in n for k in ("链接", "网址", "url", "URL",
                                   "地址", "href")):
                return ("attribute", "href")
            return ("text", None)

        schema_fields = []
        for name in field_names:
            ftype, attr = _infer_type(name)
            if ftype == "image":
                schema_fields.append({
                    "name": name, "selector": "img",
                    "type": "image", "attribute": attr})
            elif ftype == "attribute":
                schema_fields.append({
                    "name": name, "selector": "a[href*='_']",
                    "type": "attribute", "attribute": attr})
            else:
                sel = FIELD_SELECTORS.get(name, "dt a, h3 a, a")
                schema_fields.append({
                    "name": name, "selector": sel, "type": "text"})
        # 最精准的 baseSelector：笔趣阁/番茄等小说站用 .item 容器
        schema = {
            "name": "fallback",
            "baseSelector": (".item, .txt-list li, .book-list li, "
                             "ul li[class*='item'], .book-item, "
                             "div.listbox > div, .novel-list li, "
                             "article, li"),
            "fields": schema_fields,
        }

        async def _run_fb():
            conf = CrawlerRunConfig(
                cache_mode=cache,
                extraction_strategy=JsonCssExtractionStrategy(schema),
                deep_crawl_strategy=BFSDeepCrawlStrategy(
                    max_depth=deep_max_depth,
                    include_external=False,
                    max_pages=max_pages,
                ),
            )
            async with AsyncWebCrawler(config=browser_conf) as crawler:
                return await crawler.arun(url=url, config=conf)

        try:
            result = _run_async(_run_fb)
        except Exception as e:
            _report(progress, f"⚠️ fallback 也失败：{e}")
            return []

        rows = []
        seen = set()
        for r in (result if isinstance(result, list) else [result]):
            content = getattr(r, "extracted_content", "") or ""
            if not content:
                continue
            try:
                data = json.loads(content)
                items = data if isinstance(data, list) else [data]
                for row in items:
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get(next(iter(row), ""), ""))
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    rows.append(row)
            except (json.JSONDecodeError, TypeError):
                continue
        _report(progress, f"✅ fallback 拿到 {len(rows)} 行")
        return rows


# ---------- 调度器 ----------

def _pick_engine_plan(user_input: str, engine: str = "auto",
                      deep_max_depth: int = 0) -> list:
    """智能选择引擎执行方案（根据任务场景设计爬取方案）。

    规则（按场景优先，而非固定顺序）：
    - 显式指定引擎 → 只用该引擎
    - 用户说「整站/全站/所有页面」→ Crawl4AI BFS 深爬优先
    - 否则按「静态最快 → 官方库 → 兜底」设计：
      1. ScraplingEngine（秒级，最简单场景）
      2. Crawl4AIEngine（官方库，AI 生成 schema 的 CSS 提取）
      3. DirectExtractEngine（AI 直提，无需 selector）
      4. BrowserAgentEngine（自研 AI 浏览器，处理交互/反爬）
      5. BrowserUseEngine（官方 Agent，登录/复杂交互）
    """
    if engine != "auto":
        return engine  # 显式指定

    # 场景判断：深爬（只对明确指向"多页面/整站"的说法触发，
    # 「所有书籍/所有商品」只是提取全部条目，不触发深爬）
    deep_keywords = ("整站", "全站", "所有页面", "全部页面", "整个网站",
                     "全部链接", "全站所有", "所有分类", "全部分类",
                     "所有文章", "所有链接", "所有新闻", "所有帖子",
                     "全部文章", "全部商品", "全部链接", "所有商品页面",
                     "所有栏目", "全部栏目", "网站所有", "全站所有页面",
                     "爬取整个", "遍历全站")
    if any(k in user_input for k in deep_keywords):
        return "crawl4ai"

    # 默认场景：从快到慢
    return "auto"


def run_pipeline(user_input: str, url: str = "", api_key: str = "",
                 max_retries: int = 3, proxy: str = "",
                 progress: callable = None, engine: str = "auto",
                 headless: bool = True, user_data_dir: str = "",
                 **kwargs) -> EngineResult:
    """统一入口：默认走融合引擎（六引擎合体），可显式指定单引擎。

    参数：
        engine: auto=融合引擎（推荐）/ unified=融合引擎 /
                scrapling=selector / direct=AI直提 / agent=AI浏览器 /
                browser-use=官方 / crawl4ai=官方库
        headless: agent 引擎是否无头（False 时弹出浏览器窗口，可手动登录）
        user_data_dir: agent 引擎浏览器数据目录（持久化登录态）
        **kwargs: 引擎专属参数（透传给对应引擎，如 crawl4ai 的
                  deep_max_depth / max_pages / cache_mode / use_llm_extraction）
    """
    # 融合引擎优先（auto/unified 都走融合，场景分析+并行竞争+仲裁）
    if engine in ("auto", "unified"):
        try:
            from .unified_engine import UnifiedEngine
            unified = UnifiedEngine(headless=headless,
                                    user_data_dir=user_data_dir)
            return unified.run(user_input, url, api_key, max_retries,
                               proxy, progress, **kwargs)
        except ScrapeError:
            # 融合引擎整体失败 → 回退传统链（保证有兜底）
            _report(progress, "⚠️ 融合引擎失败，回退传统引擎链…")
            engine = "auto_legacy"

    from .agent_engine import BrowserAgentEngine  # 延迟导入避免循环
    all_engines = {
        "auto_legacy": [ScraplingEngine(), Crawl4AIEngine(),
                        DirectExtractEngine(),
                        BrowserAgentEngine(headless=headless,
                                           user_data_dir=user_data_dir),
                        BrowserUseEngine(headless=headless,
                                         user_data_dir=user_data_dir)],
        "scrapling": [ScraplingEngine()],
        "direct": [DirectExtractEngine()],
        "browser-use": [BrowserUseEngine(headless=headless,
                                        user_data_dir=user_data_dir)],
        "agent": [BrowserAgentEngine(headless=headless,
                                    user_data_dir=user_data_dir)],
        "crawl4ai": [Crawl4AIEngine()],
    }
    # 场景智能设计：整站 → crawl4ai 深爬
    plan = _pick_engine_plan(user_input, engine,
                             kwargs.get("deep_max_depth", 0))
    if plan == "crawl4ai":
        engines = [Crawl4AIEngine()]
    else:
        engines = all_engines.get(engine, all_engines["auto_legacy"])
    errors = []
    for eng in engines:
        if not eng.available():
            continue
        _report(progress, f"▶ 使用引擎：{eng.label}")
        try:
            # 只把引擎认识的参数传给它（避免不兼容报错）
            import inspect as _inspect
            sig = _inspect.signature(eng.run)
            eng_kwargs = {k: v for k, v in kwargs.items()
                          if k in sig.parameters}
            result = eng.run(user_input, url, api_key, max_retries,
                             proxy, progress, **eng_kwargs)
            if result.rows:
                # 链接/图片绝对化（所有引擎结果统一过，修复
                # agent 引擎路径下的相对路径渲染问题）
                result.rows = _absolutize_links(result.rows, url)
                return result
        except Exception as e:
            errors.append(f"{eng.name}: {e}")
    raise ScrapeError("所有引擎均失败：" + ("；".join(errors)
                                            if errors else "无可用引擎"))
