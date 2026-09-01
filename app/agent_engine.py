# -*- coding: utf-8 -*-
"""自研 AI 浏览器引擎（反爬模式）。

原理同 browser-use：AI 观察页面状态 → 决策下一步操作（点击/输入/滚动/提取）
→ 执行 → 重复，直到拿到数据。

浏览器能力完整移植 browser-use 官方栈（third_party/browser-use-main）：
- BrowserSession：官方浏览器会话（弹窗/登录态/持久化 user_data_dir）
- DomService：官方 DOM 序列化（开发者工具 Elements 视图，带编号+属性）
- Element：官方 CDP 元素操作（click/fill/hover，按 backend_node_id）
- page.get_elements_by_css_selector：官方 CSS 提取

AI 决策循环用 DeepSeek（官方 Agent 的 pydantic 校验 DeepSeek 过不了，
自研 JSON 循环容错更高）。引擎在 selector 模式与直提模式都失败后启用，
能处理需要交互的页面（滚动加载、点展开、翻页、简单登录输入等）。
"""
import json

from .engines import BaseEngine, EngineResult, _report, _run_async
from .llm import _call_llm, _parse_json, _get_api_key
from .scraper import ScrapeError

AGENT_SYSTEM_PROMPT = """你是浏览器操作专家。根据任务和当前页面状态，决定下一步操作。
只输出一个 JSON，格式如下：
{"action": "inspect 或 click_index 或 click 或 type 或 press 或 hover 或 scroll 或 extract 或 from_api 或 done",
 "index": 3,
 "selector": "CSS 选择器",
 "text": "要输入的文本或按键（press 时填 Enter/Escape/ArrowDown 等）",
 "fields": [{"name": "字段名", "selector": "CSS 或 JSON 路径", "type": "text 或 attr", "attr": "href"}]}

页面状态包含两个视图（都来自真实页面，相当于开发者工具）：
- 【源代码结构】：Elements 视图，缩进树显示 <标签#id.class [属性=值] » 文本>。
  字段的精确 selector 必须从这里找：先定位包含目标数据的重复结构
  （列表/卡片），看它的真实标签、class、属性，然后写 selector。
- 【可交互元素】：已编号的元素列表，点击/hover 优先用编号。

规则：
0. **写 selector 之前，先看【源代码结构】里的真实标签/class/属性**，
   不要凭空猜。例如看到 <article class="product_pod"> 里的 <h3><a>，
   selector 就写 article.product_pod h3 a（带父级路径，避免误匹配）。
1. 页面状态里的 api 字段（接口数据摘要）中有目标数据时，优先 from_api 直接提取。
2. 页面上已显示目标数据时用 extract，fields 填要提取的字段（name 用中文）。
3. 需要输入文字用 type；**在搜索框等输入后必须紧接着用 press 按 Enter 提交**（否则输入不生效），
   Esc 关闭弹窗等也用 press（text 填按键名）。
4. 需要查看悬浮内容（下拉菜单/悬浮价格）用 hover（index 指定元素）。
5. 需要滚动加载用 scroll。
6. **如果页面是登录墙/滑块/二维码（你看到"登录""扫码""验证""请输入验证码"等字样），
   直接用 done 结束（不要继续试错），让用户在浏览器里手动登录。**
7. **如果【源代码结构】信息不足**（结构太长被截断、或没看到目标数据的区域），
   用 inspect 动作细看：selector 填要查看的区域（如 .content、#main、ul 列表），
   程序会返回该区域的完整源码结构。
8. **如果页面已显示目标内容的"结果列表"**（重复的卡片/条目结构——商品、文章、视频、电影、小说、
   新闻都算），**立刻用 extract**！不要再 press/type/click！fields 至少 2-3 个
   （标题/名称、详情/价格、链接），图片类内容再加图片字段。
   selector 从【源代码结构】里的真实字段名/class 推断（如标题用 h2/h3/a，价格用含 price/价格 class）。
9. **如果 api 字段里有数据**（JSON 接口返回，selector 填 JSON 路径如 data.list[*].title），
   用 from_api 直接提取，最可靠。
10. 每步只能一个动作。"""


async def _looks_like_login_or_captcha_async(page) -> bool:
    """检测页面是否出现登录墙/滑块/二维码（官方 async page 版本）。"""
    try:
        body_text = await page.evaluate(
            "() => (document.body.innerText || '').slice(0, 2000)")
    except Exception:
        return False
    text = body_text.lower() if body_text else ""
    markers = ["登录", "扫码", "二维码", "请输入验证码", "人机验证",
               "滑块", "请完成验证", "sign in", "login", "captcha", "qr code"]
    return sum(1 for m in markers if m.lower() in text) >= 2


def _api_summary(json_data: list) -> str:
    """Network 接口摘要（给 AI 看接口数据结构，用于 from_api 决策）。"""
    if not json_data:
        return ""
    lines = []
    for item in json_data[:6]:
        url = item.get("url", "")
        data = item.get("data")
        if isinstance(data, dict):
            lines.append(f"- {url[-80:]}  object keys={list(data.keys())[:12]}")
        elif isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else data[0]
            lines.append(f"- {url[-80:]}  list len={len(data)} first={str(first)[:120]}")
        else:
            lines.append(f"- {url[-80:]}  {str(data)[:80]}")
    return "\n".join(lines)


async def _extract_rows_async(page, fields: list) -> list:
    """按字段提取（官方 page.get_elements_by_css_selector + Element）。"""
    columns = []
    for f in fields:
        sel = f.get("selector", "")
        if not sel:
            columns.append((f.get("name", "字段"), []))
            continue
        try:
            elems = await page.get_elements_by_css_selector(sel)
        except Exception:
            elems = []
        values = []
        for el in elems:
            try:
                if f.get("type") == "attr":
                    v = await el.get_attribute(f.get("attr", ""))
                    values.append(v or "")
                else:
                    v = await el.evaluate("() => (this.textContent || '').trim()")
                    values.append(str(v).strip())
            except Exception:
                values.append("")
        columns.append((f.get("name", "字段"), values))
    lens = [len(v) for _, v in columns if v]
    if not lens:
        return []
    row_count = min(lens)
    rows = []
    for i in range(row_count):
        row = {}
        for name, values in columns:
            row[name] = values[i] if i < len(values) else ""
        rows.append(row)
    return rows


def _wait_page_async(page, timeout: int = 15000):
    """等待页面稳定：networkidle + 渲染缓冲（官方 async page 版本）。"""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass


def _decide(task: str, state: str, step: int, api_key: str,
            history: str = "") -> dict:
    user_msg = f"任务：{task}\n\n步骤 {step} 的页面状态：\n{state[:4500]}"
    if history:
        user_msg += f"\n\n之前的操作（避免重复）：\n{history}"
    raw = _call_llm([
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ], api_key)
    return _parse_json(raw)


def _extract_from_api(json_data: list, fields: list) -> list:
    """从拦截到的接口 JSON 中提取数据（Network 面板直取）。"""
    from .scraper import extract_json_path

    rows = []
    for item in json_data:
        data = item.get("data")
        if not isinstance(data, (dict, list)):
            continue
        columns = []
        lens = []
        for f in fields:
            try:
                values = extract_json_path(data, f.get("selector", ""))
            except Exception:
                values = []
            values = [v if not isinstance(v, (dict, list)) else str(v)
                      for v in values]
            columns.append((f.get("name", "字段"), values))
            if values:
                lens.append(len(values))
        if not lens:
            continue
        count = min(lens)
        for i in range(count):
            row = {}
            for name, values in columns:
                row[name] = values[i] if i < len(values) else ""
            rows.append(row)
        if rows:
            break
    return rows


class BrowserAgentEngine(BaseEngine):
    """AI 浏览器引擎（反爬）。

    完整移植 browser-use 官方栈（third_party/browser-use-main）：
    - BrowserSession：官方浏览器会话（弹窗/登录态/持久化 user_data_dir）
    - DomService：官方 DOM 序列化（开发者工具 Elements 视图，带编号+属性）
    - Element：官方 CDP 元素操作（click/fill/hover，按 backend_node_id）
    - page.get_elements_by_css_selector：官方 CSS 提取
    AI 决策循环仍用 DeepSeek（官方 Agent 的 pydantic 校验 DeepSeek 过不了，
    自研 JSON 循环容错更高）。
    """

    name = "agent"
    label = "AI 浏览器引擎（反爬）"

    def __init__(self, headless: bool = True, user_data_dir: str = ""):
        from pathlib import Path
        self.headless = headless
        # 默认存到项目目录的 .chrome-data，第一次登录后下次自动恢复
        self.user_data_dir = user_data_dir or str(
            Path(__file__).resolve().parent.parent / ".chrome-data")

    def run(self, user_input, url="", api_key="", max_retries=3,
            proxy="", progress=None, max_steps: int = 12,
            headless: bool = None) -> EngineResult:
        headless = self.headless if headless is None else headless
        llm_key = _get_api_key(api_key)
        if not llm_key:
            raise ScrapeError("未配置 API Key")
        if not url:
            raise ScrapeError("AI 浏览器引擎需要目标网址")

        task = (f"打开网页 {url}，完成需求：{user_input}。"
                "如果页面需要登录或验证，先尝试处理；"
                "页面状态里会包含【源代码结构】（Elements 视图）和"
                "【api】字段（Network 接口数据摘要）。"
                "写字段 selector 时必须参考【源代码结构】里的真实"
                "标签/class/属性；如果目标数据出现在 api 接口里，"
                "用 from_api 动作直接提取（最可靠）；"
                "否则用 extract 从页面提取。")

        def _collect_api(json_data, response):
            """监听网络响应，收集 JSON 接口数据（Network 面板）。"""
            try:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype or "javascript" in ctype:
                    data = response.json()
                    json_data.append({"url": response.url, "data": data})
            except Exception:
                pass

        _report(progress, "⑥ 启动 AI 浏览器引擎：AI 正在观察并操作页面…")

        # ---------- 异步主流程（官方 BrowserSession + DomService + Element） ----------
        async def _agent_flow():
            from browser_use.browser.session import BrowserSession
            from browser_use.dom.service import DomService
            from browser_use.actor.element import Element

            session = BrowserSession(
                headless=headless,
                user_data_dir=self.user_data_dir or None,
            )
            await session.start()
            try:
                import asyncio
                json_data = []  # Network 接口数据（from_api 用）
                # 打开目标页（登录/验证需要真实页面）
                try:
                    await session.navigate_to(url)
                except Exception as e:
                    _report(progress, f"⚠️ 首次打开页面异常：{e}，继续…")
                await asyncio.sleep(2)

                page = await session.must_get_current_page()
                # 监听接口响应（Network 面板）
                try:
                    page.on("response", lambda r: _collect_api(json_data, r))
                except Exception:
                    pass
                try:
                    await page.evaluate("() => { window.__agent_scrape = true; }")
                except Exception:
                    pass

                # 等异步接口加载完（fetch/XHR），再给 AI 看状态
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # 有头模式：暂停 8 秒给用户登录/验证的时间
                if not headless:
                    _report(progress, "🌐 浏览器已打开！如需登录/滑块验证，"
                                      "请在弹出的窗口中手动操作（8 秒后开始；"
                                      "登录态会保存到数据目录，下次自动恢复）")
                    page.wait_for_timeout(8000)

                # 登录墙/滑块 → 等待用户人工介入（而不是直接退出）
                if await _looks_like_login_or_captcha_async(page):
                    if headless:
                        raise ScrapeError("页面需要登录/滑块验证，"
                                          "无头模式无法人工介入，请勾选"
                                          "「显示浏览器窗口」后重试")
                    _report(progress, "⚠️ 检测到登录墙/滑块。"
                                      "请在浏览器窗口中完成登录/验证，"
                                      "完成后程序自动继续爬取（最多等 90 秒）…")
                    waited = 0
                    while waited < 90:
                        page.wait_for_timeout(3000)
                        waited += 3
                        if not await _looks_like_login_or_captcha_async(page):
                            _report(progress, f"✅ 登录/验证完成"
                                              f"（等待 {waited} 秒），"
                                              f"继续爬取…")
                            break
                        _report(progress, f"⏳ 等待您登录/验证…"
                                          f"（已等待 {waited} 秒）")
                    else:
                        raise ScrapeError("等待人工登录超时（90 秒），"
                                          "请确认已登录/完成验证后重试")

                # ---------- AI 决策循环 ----------
                rows = []
                used_steps = 0
                last_action_key = None
                repeat = 0
                last_url = None
                last_type_pos = None
                type_repeat = 0
                action_history = []  # 行动历史（喂给 AI，防重复）
                dom_service = DomService(browser_session=session)
                dom_state = None

                for step in range(1, max_steps + 1):
                    used_steps = step
                    # URL 变化 → 页面已跳转，重置重复计数（不是死循环）
                    cur_url = await page.get_url()
                    if last_url is not None and cur_url != last_url:
                        repeat = 0
                        type_repeat = 0
                        _report(progress, f"↳ 页面已跳转：{cur_url[:70]}")
                    last_url = cur_url

                    # 官方 DomService：完整 Elements 视图（带编号 + class/id/href 等）
                    try:
                        dom_state, _, _ = await dom_service.get_serialized_dom_tree()
                        dom_view = dom_state.llm_representation(
                            include_attributes=["class", "id", "href",
                                                "title", "src", "alt",
                                                "name", "type", "value"])
                    except Exception as e:
                        dom_view = ""
                        _report(progress, f"⚠️ DOM 视图获取失败：{e}")

                    # Network 接口摘要（不依赖浏览器，直接传 json_data）
                    api_summary = _api_summary(json_data)
                    state = (
                        f"【源代码结构】（开发者工具 Elements 视图，缩进=层级，"
                        f"[编号] 是可交互元素，写 selector 参考这里的真实"
                        f"标签/class/属性）：\n{dom_view[:5000]}"
                        + (f"\n\n【api 接口摘要】：\n{api_summary}" if api_summary else "")
                        + f"\n\n当前 URL：{cur_url}"
                    )

                    # 搜索 URL 检测：URL 含搜索参数 → 提醒 AI 已在结果页
                    low_url = cur_url.lower()
                    if any(k in low_url for k in ("q=", "keyword", "search",
                                                  "query", "s?word",
                                                  "so.com/s")):
                        state += ("\n[提示] 当前是搜索结果页（URL 含搜索参数），"
                                  "若页面已显示结果列表请直接 extract，不要再搜索。")
                    _report(progress, f"⏳ 步骤 {step}：AI 正在分析页面…")
                    history_text = "\n".join(
                        f"步骤{i+1}: {h}" for i, h in
                        enumerate(action_history[-5:]))  # 最多记最近 5 步
                    decision = _decide(task, state, step, llm_key, history_text)
                    action = decision.get("action", "done")
                    sel = str(decision.get("selector", ""))[:100]
                    idx_str = str(decision.get("index", ""))
                    _report(progress,
                            f"⏳ 步骤 {step}：AI 决定 → {action} {sel}{idx_str}")

                    if action == "inspect":
                        # AI 想细看某区域源码 → 用官方 get_elements_by_css_selector
                        # 定位区域，输出其 DOM 结构（查源代码）
                        try:
                            elems = await page.get_elements_by_css_selector(sel)
                            if elems:
                                info = await elems[0].get_basic_info()
                                _report(progress,
                                        f"🔍 查看区域 {sel} 的源代码："
                                        f"{str(info)[:600]}")
                            else:
                                _report(progress, f"🔍 区域 {sel} 未找到元素")
                        except Exception as e:
                            _report(progress, f"⚠️ inspect 失败：{e}")
                    elif action == "from_api":
                        # AI 的主 selector（如 products[*]）+ 字段相对路径（如 name）
                        # → 拼接成 products[*].name
                        base = str(decision.get("selector", "")).strip()
                        api_fields = []
                        for f in decision.get("fields", []):
                            fsel = str(f.get("selector", "")).strip()
                            if fsel and base and not fsel.startswith(("$", "data", "[")):
                                fsel = f"{base}.{fsel}"
                            api_fields.append({**f, "selector": fsel})
                        rows = _extract_from_api(json_data, api_fields)
                        if rows:
                            _report(progress,
                                    f"✅ 从接口提取完成：{len(rows)} 行")
                            break
                        _report(progress, "⚠️ 接口提取为空，继续操作…")
                    elif action == "extract":
                        rows = await _extract_rows_async(
                            page, decision.get("fields", []))
                        if rows:
                            _report(progress, f"✅ AI 浏览器引擎完成：{len(rows)} 行")
                            break
                        _report(progress, "⚠️ 提取为空，继续操作…")
                    elif action == "done":
                        break
                    elif action == "click_index":
                        try:
                            idx = int(decision.get("index", 0) or 0)
                            node = await session.get_dom_element_by_index(idx)
                            if node and node.backend_node_id:
                                elem = Element(session, node.backend_node_id,
                                               getattr(node, "session_id", None))
                                # 官方 Element.click() 的坐标在 scrollIntoView 之前
                                # 计算，视口外元素会被 clamp 到错误位置点击
                                # （官方 Agent 是点击前先滚动）。这里先滚动再点。
                                try:
                                    await elem.evaluate(
                                        "() => { this.scrollIntoView("
                                        "{block:'center'}); return true; }")
                                    await asyncio.sleep(0.5)
                                except Exception:
                                    pass
                                await elem.click()
                                _wait_page_async(page)
                            else:
                                _report(progress, f"⚠️ 编号 {idx} 不存在，"
                                                  f"尝试滚动继续")
                                try:
                                    await page.mouse.wheel(0, 600)
                                except Exception:
                                    pass
                        except Exception:
                            try:
                                await page.mouse.wheel(0, 600)
                            except Exception:
                                pass
                    elif action == "click":
                        try:
                            if sel.startswith("http"):
                                await page.goto(sel)
                                _wait_page_async(page)
                            else:
                                elems = await page.get_elements_by_css_selector(sel)
                                if elems:
                                    await elems[0].click()
                                    _wait_page_async(page)
                                else:
                                    raise RuntimeError("未找到元素")
                        except Exception:
                            _report(progress, "⚠️ 点击失败，尝试滚动继续")
                            try:
                                await page.mouse.wheel(0, 600)
                            except Exception:
                                pass
                    elif action == "hover":
                        try:
                            idx = int(decision.get("index", 0) or 0)
                            node = await session.get_dom_element_by_index(idx)
                            if node and node.backend_node_id:
                                elem = Element(session, node.backend_node_id,
                                               getattr(node, "session_id", None))
                                try:
                                    await elem.evaluate(
                                        "() => { this.scrollIntoView("
                                        "{block:'center'}); return true; }")
                                    await asyncio.sleep(0.3)
                                except Exception:
                                    pass
                                await elem.hover()
                        except Exception:
                            pass
                    elif action == "press":
                        try:
                            press_key = str(decision.get("text", "") or
                                            decision.get("key", "") or "Enter")
                            await page.press(press_key)
                            _wait_page_async(page)
                        except Exception:
                            pass
                    elif action == "type":
                        text_val = str(decision.get("text", ""))
                        idx_val = decision.get("index", 0) or 0
                        # 兼容：AI 把编号当 selector 写成 "2"
                        if not idx_val and sel and sel.isdigit():
                            idx_val = int(sel)
                        filled = False
                        try:
                            if idx_val:
                                node = await session.get_dom_element_by_index(
                                    int(idx_val))
                                if node and node.backend_node_id:
                                    elem = Element(session, node.backend_node_id,
                                                   getattr(node, "session_id", None))
                                    try:
                                        await elem.evaluate(
                                            "() => { this.scrollIntoView("
                                            "{block:'center'}); return true; }")
                                        await asyncio.sleep(0.3)
                                    except Exception:
                                        pass
                                    await elem.fill(text_val)
                                    filled = True
                            elif sel:
                                elems = await page.get_elements_by_css_selector(sel)
                                if elems:
                                    await elems[0].fill(text_val)
                                    filled = True
                        except Exception:
                            filled = False
                        # 注意：不自动按 Enter（否则与 AI 自己的 press 双重提交，
                        # 造成多次搜索）。由 AI 决策 press 提交；仅当 AI 连续
                        # 2 次 type 同一位置（页面没变化）才强制补 Enter 兜底。
                        pos_key = f"type:{idx_val or sel}"
                        if pos_key == last_type_pos:
                            type_repeat += 1
                            if type_repeat >= 2:
                                try:
                                    await page.press("Enter")
                                    _wait_page_async(page)
                                except Exception:
                                    pass
                                type_repeat = 0
                        else:
                            type_repeat = 0
                        last_type_pos = pos_key
                    elif action == "scroll":
                        try:
                            await page.mouse.wheel(0, 700)
                        except Exception:
                            pass
                    else:
                        break
                    # 防死循环：相同动作连续 5 次就停
                    action_key = (action, str(decision.get("selector", "")))
                    repeat = repeat + 1 if action_key == last_action_key else 0
                    # press/click/scroll 重复 ≥2 → 页面可能已有结果但 AI 没提取，
                    # 强制自动提取一次（通用字段兜底，别空手而归）
                    if action in ("press", "click_index", "click",
                                  "scroll") and repeat >= 2:
                        try:
                            forced = await _extract_rows_async(page, [
                                {"name": "标题", "selector":
                                    "h2, h3, h4, .title, [class*=title]",
                                 "type": "text"},
                                {"name": "详情", "selector":
                                    "p, .desc, [class*=desc], [class*=price], "
                                    "[class*=item], li",
                                 "type": "text"},
                            ])
                            if forced:
                                rows = forced
                                _report(progress,
                                        f"✅ 检测到重复操作，自动提取 "
                                        f"{len(rows)} 行")
                                break
                        except Exception:
                            pass
                    # 记录行动历史（喂给 AI，避免重复操作）
                    result_desc = ""
                    if action in ("extract", "from_api"):
                        result_desc = f"（提取 {'成功' if rows else '为空'}）"
                    action_history.append(f"{action} {sel}{idx_str}{result_desc}")

                    # inspect 只查看源码不改页面，直接进入下一轮
                    # （不触发自动提取兜底，避免抢在 AI 的 extract 之前）
                    if action == "inspect":
                        last_action_key = action_key
                        continue

                    # 程序级兜底（借鉴 browser-use 多动作思想）：AI 每执行一个
                    # 交互动作（点击/输入/滚动）后，自动检测页面是否已出现
                    # 结构化数据。若 AI 一直不 extract，程序也会在数据出现后
                    # 自动交付，不再空手而归。
                    try:
                        auto = await _extract_rows_async(page, [
                            {"name": "标题", "selector":
                                "h2, h3, h4, .title, [class*=title]",
                             "type": "text"},
                            {"name": "详情", "selector":
                                "[class*=price], [class*=desc], [class*=intro], "
                                "[class*=summary], p",
                             "type": "text"},
                        ])
                        # 过滤：至少 3 行 + 每行标题有实际内容
                        auto = [r for r in auto
                                if str(r.get("标题", "")).strip()
                                and len(str(r.get("标题", "")).strip()) > 4]
                        if len(auto) >= 3:
                            rows = auto
                            _report(progress,
                                    f"✅ 动作后自动检测到页面数据："
                                    f"{len(auto)} 行")
                            break
                    except Exception:
                        pass
                    if repeat >= 5:
                        _report(progress, "⚠️ 检测到重复动作，停止")
                        break
                    last_action_key = action_key

                return rows, used_steps
            finally:
                try:
                    await session.kill()
                except Exception:
                    pass

        rows, used_steps = _run_async(_agent_flow)

        if not rows:
            raise ScrapeError("AI 浏览器引擎未提取到数据（可能被风控拦截）")
        return EngineResult(rows=rows, status=0, used_fetcher="agent",
                            engine=self.name, attempts=used_steps)
