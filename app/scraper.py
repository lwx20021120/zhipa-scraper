# -*- coding: utf-8 -*-
"""Scrapling 执行器：封装三种 Fetcher，提供按字段抓取的能力。

数据模型
--------
fields: list[dict]，每个字段：
    {"name": "标题", "selector": "h2 a", "type": "text|attr|html", "attr": "href"}

抓取结果 rows: list[dict]，按"主键字段"（fields[0]）的长度对齐成行。
"""
import json
import re

from scrapling.fetchers import Fetcher, DynamicFetcher, StealthyFetcher

FETCHER_MAP = {
    "static": ("静态", Fetcher.get),
    "dynamic": ("动态", DynamicFetcher.fetch),
    "stealthy": ("伪装", StealthyFetcher.fetch),
}

# 中文名 -> 键
FETCHER_ALIAS = {
    "静态": "static",
    "动态": "dynamic",
    "伪装": "stealthy",
}

DEFAULT_PAGE_STEP = 25  # 默认每页条数（豆瓣等列表型网站常用）


class ScrapeError(Exception):
    """抓取过程中的业务错误。"""


def fetch_page(url: str, fetcher: str = "static", wait_seconds: int = 2,
               proxy: str = "", capture_xhr: str = None) -> object:
    """按类型抓取页面，返回 scrapling Response。

    capture_xhr: 正则模式，动态/伪装模式下拦截匹配的 XHR 请求
                 （如 '.*' 拦截全部，用于接口型网站）。
    """
    key = FETCHER_ALIAS.get(fetcher, fetcher)
    if key not in FETCHER_MAP:
        raise ScrapeError(f"未知抓取器：{fetcher}")

    if key == "static":
        kwargs = {}
        if proxy:
            kwargs["proxy"] = proxy
        try:
            return Fetcher.get(url, **kwargs)
        except Exception as e:
            raise ScrapeError(f"静态抓取失败：{e}") from e

    kwargs = {"wait_seconds": wait_seconds}
    if proxy:
        kwargs["proxy"] = proxy
    if capture_xhr:
        kwargs["capture_xhr"] = capture_xhr
    fn = FETCHER_MAP[key][1]
    try:
        return fn(url, **kwargs)
    except TypeError:
        try:
            return fn(url)
        except Exception as e:
            raise ScrapeError(f"浏览器抓取失败：{e}") from e
    except Exception as e:
        raise ScrapeError(f"浏览器抓取失败：{e}") from e


def _extract_one(el, ftype: str, attr: str = "") -> str:
    """从单个元素按类型提取值。"""
    if ftype == "attr":
        return el.attrib.get(attr, "") if attr else ""
    if ftype == "html":
        return getattr(el, "html_content", "") or ""
    if ftype == "image":
        # 图片地址：优先 src，其次 data-src（懒加载）
        return (el.attrib.get("src") or el.attrib.get("data-src")
                or el.attrib.get("data-original") or "").strip()
    return (el.text or "").strip()


def _try_extract(page, field: dict) -> list:
    """对一个字段从页面提取值列表（出错返回空列表）。"""
    try:
        els = page.css(field["selector"])
    except Exception:
        return []
    return [_extract_one(el, field.get("type", "text"), field.get("attr", ""))
            for el in els]


def extract_json_path(data, path: str) -> list:
    """简化 JSONPath 提取，支持 data.products[*].name 语法。

    返回匹配到的所有值列表。
    """
    tokens = re.findall(r"[^.\[]+|\[\*\]", path)
    results = [data]
    for t in tokens:
        if t == "[*]":
            new = []
            for item in results:
                if isinstance(item, list):
                    new.extend(item)
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, list):
                            new.extend(v)
            results = new
        else:
            new = []
            for item in results:
                if isinstance(item, dict) and t in item:
                    new.append(item[t])
                elif isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, dict) and t in sub:
                            new.append(sub[t])
            results = new
    return results


def _scrape_json(url: str, fetcher: str, fields: list, wait_seconds: int,
                 proxy: str = "", pagination: dict = None) -> tuple:
    """接口直取模式：拦截 XHR，从 JSON 响应里提取字段。

    fields 的 type 为 "json"，selector 存 JSON 路径（如 data.products[*].name）。
    返回 (rows, status)。
    """
    pages = []
    if pagination and pagination.get("mode") == "url_pattern":
        try:
            max_pages = max(1, int(pagination.get("max_pages", 1) or 1))
        except (TypeError, ValueError):
            max_pages = 1
        try:
            step = max(1, int(pagination.get("step", DEFAULT_PAGE_STEP) or DEFAULT_PAGE_STEP))
        except (TypeError, ValueError):
            step = DEFAULT_PAGE_STEP
        try:
            start = max(0, int(pagination.get("start", 0) or 0))
        except (TypeError, ValueError):
            start = 0
        pattern = pagination.get("url_pattern", "")
        if pattern:
            for i in range(max_pages):
                pages.append(pattern.replace("{page}", str(start + i * step)))
    if not pages:
        pages = [url]

    # 收集所有 XHR 的 JSON 响应
    json_responses = []
    status = 0
    for page_url in pages:
        try:
            page = fetch_page(page_url, fetcher, wait_seconds, proxy,
                              capture_xhr=".*")
            status = getattr(page, "status", 0)
            for x in getattr(page, "captured_xhr", []) or []:
                body = getattr(x, "body", None)
                if not body:
                    continue
                if isinstance(body, bytes):
                    body = body.decode("utf-8", "ignore")
                try:
                    json_responses.append(json.loads(body))
                except (json.JSONDecodeError, ValueError):
                    continue
        except ScrapeError:
            continue

    if not json_responses:
        raise ScrapeError("没有捕获到 JSON 接口数据（页面可能没有 XHR 请求）")

    # 尝试每个 JSON 响应，提取全部字段
    rows = []
    for data in json_responses:
        columns = []
        min_len = None
        for f in fields:
            values = extract_json_path(data, f.get("selector", ""))
            values = [v if not isinstance(v, (dict, list)) else str(v) for v in values]
            columns.append((f["name"], values))
            if values:
                min_len = len(values) if min_len is None else min(min_len, len(values))
        if not min_len:
            continue
        for i in range(min_len):
            row = {}
            for name, values in columns:
                row[name] = values[i] if i < len(values) else ""
            rows.append(row)

    if not rows:
        raise ScrapeError("JSON 接口里没有找到匹配的字段路径")
    return rows, status


def _smart_extract(page, field: dict) -> tuple:
    """智能提取：匹配空时自动简化 selector。

    策略：
    1. 先按原 selector 抓，至少有一个非空值才算成功
    2. 否则逐步去掉"中间段"（保留首尾，如 .star），看能否命中
    3. 再退回逐步去掉最后段

    返回 (values, used_selector)。
    """
    original = field["selector"]
    values = _try_extract(page, field)
    if any(v.strip() for v in values):
        return values, original

    parts = original.split()
    # 策略1：逐步去掉中间段（保留首尾）
    if len(parts) >= 3:
        for i in range(1, len(parts) - 1):
            reduced_sel = " ".join(parts[:i] + parts[i + 1:])
            values = _try_extract(page, {**field, "selector": reduced_sel})
            if any(v.strip() for v in values):
                return values, reduced_sel

    # 策略2：逐步去掉最后段
    sel = original
    for _ in range(5):
        if " " not in sel:
            break
        sel = " ".join(sel.split()[:-1])
        if not sel:
            break
        values = _try_extract(page, {**field, "selector": sel})
        if any(v.strip() for v in values):
            return values, sel

    return [], original


def _scrape_single(url: str, fetcher: str, fields: list, wait_seconds: int,
                   proxy: str = "", return_body: bool = False) -> tuple:
    """单页抓取：按 fields[0]（主键字段）的长度作为行数，其他字段按下标对齐。

    返回 (rows, status[, body])。
    """
    page = fetch_page(url, fetcher, wait_seconds, proxy)
    status = getattr(page, "status", 0)

    columns = []
    for f in fields:
        values, used_sel = _smart_extract(page, f)
        columns.append((f["name"], values))

    # 行数 = 各字段有效匹配数的最小值（保证所有字段都有值，
    # 避免主键字段命中多个变体导致行数膨胀）
    non_zero_lens = [len(v) for _, v in columns if v]
    if not non_zero_lens:
        raise ScrapeError("没有提取到任何数据，请检查选择器是否正确")
    row_count = min(non_zero_lens)

    rows = []
    for i in range(row_count):
        row = {}
        for name, values in columns:
            row[name] = values[i] if i < len(values) else ""
        rows.append(row)

    if return_body:
        return rows, status, getattr(page, "body", "") or ""
    return rows, status


def scrape(url: str, fetcher: str = "static", fields: list = None,
           wait_seconds: int = 2, proxy: str = "", return_body: bool = False,
           pagination: dict = None) -> tuple:
    """执行抓取（支持 url_pattern 翻页）。

    返回 (rows, status[, body])。

    pagination:
        {"mode": "none|next_button|url_pattern",
         "url_pattern": "https://x?start={page}",
         "next_selector": "a.next",
         "max_pages": 5, "step": 25}
    """
    fields = fields or []
    if not url.strip():
        raise ScrapeError("请输入目标网址")
    if not fields:
        raise ScrapeError("请至少添加一个字段")

    # 接口直取模式：存在 type=json 字段时走 XHR 拦截提取
    if any(f.get("type") == "json" for f in fields):
        return _scrape_json(url, fetcher, fields, wait_seconds, proxy, pagination)

    pagination = pagination or {"mode": "none"}
    mode = pagination.get("mode", "none")

    # BFS 整站深爬（替代 Crawl4AI 深爬）
    if mode == "deep":
        from .deep_crawler import DeepCrawler
        try:
            max_pages = max(1, int(pagination.get("max_pages", 15) or 15))
        except (TypeError, ValueError):
            max_pages = 15
        crawler = DeepCrawler(url, max_pages=max_pages,
                              same_domain_only=True)
        rows = crawler.crawl(fields, fetcher, wait_seconds, proxy)
        if not rows:
            raise ScrapeError("深爬没有提取到任何数据")
        if return_body:
            return rows, 200, ""
        return rows, 200

    if mode == "url_pattern" and pagination.get("url_pattern"):
        pattern = pagination["url_pattern"]
        try:
            max_pages = max(1, int(pagination.get("max_pages", 1) or 1))
        except (TypeError, ValueError):
            max_pages = 1
        try:
            step = max(1, int(pagination.get("step", DEFAULT_PAGE_STEP) or DEFAULT_PAGE_STEP))
        except (TypeError, ValueError):
            step = DEFAULT_PAGE_STEP
        try:
            start = max(0, int(pagination.get("start", 0) or 0))
        except (TypeError, ValueError):
            start = 0

        all_rows = []
        last_status = 0
        last_body = ""
        page_val = start
        for i in range(max_pages):
            page_url = pattern.replace("{page}", str(page_val))
            rows = []
            try:
                if return_body:
                    rows, status, body = _scrape_single(
                        page_url, fetcher, fields, wait_seconds, proxy,
                        return_body=True)
                    last_body = body if body else last_body
                else:
                    rows, status = _scrape_single(
                        page_url, fetcher, fields, wait_seconds, proxy)
                last_status = status
            except ScrapeError:
                # 首页失败且从 0 开始 → 自动尝试从 1 开始
                # （适配 page-{page}.html 这类页码从 1 开始的网站）
                if i == 0 and start == 0:
                    alt_url = pattern.replace("{page}", "1")
                    if alt_url == page_url:
                        raise
                    try:
                        if return_body:
                            rows, status, body = _scrape_single(
                                alt_url, fetcher, fields, wait_seconds, proxy,
                                return_body=True)
                            last_body = body if body else last_body
                        else:
                            rows, status = _scrape_single(
                                alt_url, fetcher, fields, wait_seconds, proxy)
                        last_status = status
                        page_val = 1
                    except ScrapeError as e2:
                        raise ScrapeError(f"第 1 页失败：{e2}") from e2
                else:
                    raise

            if i == 0:
                # 自适应步长：首页行数少于 step → 视为页码式翻页（每页 +1）
                if rows and step > len(rows):
                    step = 1
            all_rows.extend(rows)
            # 本页一条都没抓到说明到了末尾，提前结束
            if not rows:
                break
            page_val += step

        if not all_rows:
            raise ScrapeError("翻页后没有任何数据，请检查翻页规则")
        if return_body:
            return all_rows, last_status, last_body
        return all_rows, last_status

    # next_button 翻页暂未实现（P3 再说），按单页抓取
    return _scrape_single(url, fetcher, fields, wait_seconds, proxy, return_body)


def auto_fetch(url: str, fields: list = None, wait_seconds: int = 2,
               proxy: str = "", pagination: dict = None) -> tuple:
    """自动模式：静态 -> 动态 -> 伪装 逐级升级，直到抓到数据。

    返回 (rows, status, used_fetcher)。
    """
    last_err = None
    for key in ("static", "dynamic", "stealthy"):
        try:
            rows, status = scrape(url, key, fields, wait_seconds, proxy,
                                  pagination=pagination)
            if rows:
                return rows, status, key
        except ScrapeError as e:
            last_err = e
    raise ScrapeError(f"自动模式全部失败（{last_err}）" if last_err
                      else "自动模式全部失败")