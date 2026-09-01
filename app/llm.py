# -*- coding: utf-8 -*-
"""AI 配置器：调用 DeepSeek，把自然语言转成爬取配置 JSON；失败时修正选择器。

依赖 openai 库（已装），兼容 DeepSeek 的 OpenAI 格式接口。
"""
import json
import re

from openai import OpenAI

from .config import load_config

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

SYSTEM_PROMPT = """你是网页爬取配置专家。用户会描述想从某个网页采集什么数据，你需要输出一份 JSON 爬取配置。

输出格式必须严格如下（只输出 JSON，不要任何解释文字）：
{
  "url": "目标网页地址",
  "fetcher": "auto 或 static 或 dynamic 或 stealthy",
  "fields": [
    {"name": "字段中文名", "selector": "标准 CSS 选择器", "type": "text 或 attr 或 html", "attr": "type 为 attr 时填属性名如 href，否则填空"}
  ],
  "pagination": {"mode": "none 或 next_button 或 url_pattern", "next_selector": "下一页按钮的 CSS 选择器", "url_pattern": "分页 URL 模板，用 {page} 占位", "max_pages": 1},
  "wait_seconds": 2
}

规则：
1. selector 必须是**带父级路径限定**的标准 CSS 选择器（如 ul li h3、.item .title、ol.grid_view .rating_num），
   不要使用全局太宽泛的选择器（如 .title、.rating_num），否则会匹配到页面其他无关元素，导致行数膨胀。
   如果提供了"页面内容片段"，必须先分析片段里的真实标签和 class，用片段中实际存在的元素构造 selector，
   不要凭空猜测。注意有的元素可能嵌套（如 .rating_num 不一定在 .star 内），要以片段中的真实结构为准。
2. type 为 attr 时必须填写 attr 字段；text 类型 attr 填空。
3. fetcher 选择依据：纯静态页面用 static；需要 JS 渲染/点击/滚动的用 dynamic；有明显反爬（验证码、检测自动化）的用 stealthy；不确定一律 auto。
4. 用户没提到翻页时，pagination.mode 用 none。
5. 用户提到的每个数据项都要对应一个 field，字段 name 用中文。
6. wait_seconds：动态或伪装页面给 3，静态给 1。
7. 翻页用 url_pattern 时：
   - 页码式 URL（如 page-{page}.html、?p={page}）：start 填 1，step 填 1
   - 偏移式 URL（如 ?start={page}、?offset={page}）：start 填 0，step 填每页条数（如 25）
8. 不确定的字段类型用 text；链接用 attr + href。
9. 如果目标是接口型网站（电商、行情、资讯列表等，数据明显由 AJAX 接口返回），
   字段 type 用 "json"，selector 填 JSON 路径（如 data.products[*].name），
   程序会自动拦截 XHR 请求从接口 JSON 里提取数据，比解析 DOM 更稳。
10. 用户要求爬"整站/全站/所有页面/每个分类"时，pagination.mode 用 "deep"，
   max_pages 填页面数上限（默认 15），程序会 BFS 自动爬取全站同域名页面。
"""


def _get_api_key(api_key: str = "") -> str:
    """获取 API Key：参数优先，否则读 config.json。"""
    key = api_key.strip()
    if key:
        return key
    return load_config().get("api_key", "").strip()


def _parse_json(text: str):
    """宽容地从 LLM 输出解析 JSON：处理代码块包裹、多 JSON、换行等。"""
    text = text.strip()
    if not text:
        raise json.JSONDecodeError("empty", text, 0)
    # 先去掉 markdown 代码块包裹
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # 找第一个 { 或 [ 的位置，从那里开始解析（json.JSONDecoder.raw_decode 容忍前导空白）
    for i, c in enumerate(text):
        if c in "{[":
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    # fallback：整段解析（会抛错，给出原始错误）
    return json.loads(text)


def _normalize_config(cfg: dict) -> dict:
    """补齐/校正配置字段，确保结构完整。"""
    fields = []
    for f in cfg.get("fields", []):
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", "")).strip() or "字段"
        selector = str(f.get("selector", "")).strip()
        if not selector:
            continue
        fields.append({
            "name": name,
            "selector": selector,
            "type": f.get("type", "text") if f.get("type") in ("text", "attr", "html", "json", "image") else "text",
            "attr": str(f.get("attr", "")).strip(),
        })
    fetcher = cfg.get("fetcher", "auto")
    if fetcher not in ("auto", "static", "dynamic", "stealthy"):
        fetcher = "auto"
    pag = cfg.get("pagination") or {}
    if not isinstance(pag, dict):
        pag = {}
    if pag.get("mode") not in ("none", "next_button", "url_pattern", "deep"):
        pag["mode"] = "none"
    try:
        wait = max(0, min(int(cfg.get("wait_seconds", 2)), 60))
    except (TypeError, ValueError):
        wait = 2
    mode = pag.get("mode", "none")
    if mode not in ("none", "next_button", "url_pattern", "deep"):
        mode = "none"
    default_pages = 15 if mode == "deep" else 1
    return {
        "url": str(cfg.get("url", "")).strip(),
        "fetcher": fetcher,
        "fields": fields,
        "pagination": {
            "mode": mode,
            "next_selector": str(pag.get("next_selector", "")).strip(),
            "url_pattern": str(pag.get("url_pattern", "")).strip(),
            "max_pages": max(1, int(pag.get("max_pages", default_pages) or default_pages)),
            "start": max(0, int(pag.get("start", 0) or 0)),
            "step": max(1, int(pag.get("step", 25) or 25)),
        },
        "wait_seconds": wait,
    }


def _call_llm(messages: list, api_key: str, retries: int = 3) -> str:
    """调用 DeepSeek，返回文本。401/429/5xx 等临时错误自动重试。"""
    import time

    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    last_err = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                response_format={"type": "json_object"},
                messages=messages,
                temperature=0.2,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last_err


def generate_config(user_input: str, url: str = "", api_key: str = "",
                    page_snippet: str = "") -> dict:
    """自然语言 → 爬取配置 JSON。

    参数：
        user_input: 用户的一句话需求，如"爬商品价格和标题，翻 3 页"
        url: 目标网址（可选，LLM 可能自己推断）
        page_snippet: 页面 HTML 片段（可选，辅助生成选择器）
        api_key: DeepSeek Key（留空则读 config.json）
    返回：规范化后的配置 dict。无 Key 时抛 ValueError。
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("未配置 DeepSeek API Key，请先在设置中填写")

    parts = [f"用户需求：{user_input.strip()}"]
    if url.strip():
        parts.append(f"目标网址：{url.strip()}")
    if page_snippet:
        parts.append(f"页面内容片段（用于判断选择器）：\n{page_snippet[:1500]}")
    user_msg = "\n".join(parts)

    raw = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ], key)

    cfg = _normalize_config(_parse_json(raw))
    if url.strip() and not cfg["url"]:
        cfg["url"] = url.strip()
    if not cfg["fields"]:
        raise ValueError("AI 没有生成有效的字段配置，请换一种说法再试")
    return cfg


def fix_config(user_input: str, config: dict, error_msg: str,
               page_snippet: str, api_key: str = "") -> dict:
    """抓取失败时，让 AI 根据错误信息修正配置。

    返回修正后的配置 dict。
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("未配置 DeepSeek API Key")

    user_msg = (
        f"上次的需求：{user_input.strip()}\n"
        f"上次的配置：{json.dumps(config, ensure_ascii=False)}\n"
        f"执行失败原因：{error_msg}\n"
        f"请修正配置后重新输出完整的 JSON。\n"
        f"页面内容片段：\n{page_snippet[:2000]}"
    )
    raw = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ], key)
    cfg = _normalize_config(_parse_json(raw))
    if not cfg["url"]:
        cfg["url"] = config["url"]
    return cfg


def _split_chunks(text: str, size: int) -> list:
    """按段落分块（不截断句子），借鉴 Crawl4AI chunking。"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


def _extract_one_chunk(user_input: str, chunk: str, api_key: str,
                       max_items: int) -> list:
    """单块 LLM 提取（含一次重试）。"""
    system = (
        "你是网页数据提取助手。用户会给出网页文本和需求，"
        "你从文本中提取用户要求的数据，输出 JSON 数组。\n"
        "规则：\n"
        f"1. 最多提取 {max_items} 条，只输出 JSON 数组，不要输出解释。\n"
        "2. 严格只提取文本中真实存在的内容，不要编造。\n"
        "3. 每条是对象，键用用户需求里的字段名（中文）。\n"
        "4. 找不到的数据字段填空字符串。\n"
        "5. 输出示例：[{\"标题\":\"...\",\"价格\":\"...\"}]"
    )
    raw = _call_llm([
        {"role": "system", "content": system},
        {"role": "user",
         "content": f"需求：{user_input.strip()}\n\n网页文本：\n{chunk}"},
    ], api_key)
    try:
        data = _parse_json(raw)
    except json.JSONDecodeError:
        raw = _call_llm([
            {"role": "system", "content": system},
            {"role": "user",
             "content": f"上次输出不是合法 JSON，请只输出 JSON 数组。\n需求：{user_input.strip()}\n\n网页文本：\n{chunk}"},
        ], api_key)
        data = _parse_json(raw)
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def direct_extract(user_input: str, page_text: str, api_key: str = "",
                   max_items: int = 100, chunk_size: int = 5000,
                   max_chunks: int = 4) -> list:
    """AI 直提模式：让 AI 从页面文本中直接提取用户要的数据。

    长文本先分块，再用 BM25 选出与需求最相关的块（正文不在开头时也
    能找到关键内容），每块 LLM 提取后合并。

    参数：
        user_input: 用户需求（如"提取商品标题、价格、链接"）
        page_text: 页面渲染后的文本（markdown / 去噪正文）
        api_key: DeepSeek Key
        max_items: 最多提取条数
        chunk_size: 每块字符数（防 token 爆炸）
        max_chunks: 最多处理块数（防过多调用）
    返回：list[dict]。失败抛 ValueError。
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("未配置 DeepSeek API Key")

    chunks = _split_chunks(page_text, chunk_size)

    # BM25 相关性选块：优先处理与需求最相关的块
    try:
        from .bm25 import bm25_rank
        ordered = bm25_rank(user_input, chunks,
                            top_k=min(max_chunks, len(chunks)))
    except Exception:
        ordered = chunks[:max_chunks]

    all_rows = []
    for chunk in ordered:
        remain = max_items - len(all_rows)
        if remain <= 0:
            break
        try:
            rows = _extract_one_chunk(user_input, chunk, key, remain)
        except Exception:
            continue
        all_rows.extend(rows)

    if not all_rows:
        raise ValueError("AI 直提没有提取到任何数据")
    return all_rows[:max_items]
