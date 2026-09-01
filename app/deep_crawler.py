# -*- coding: utf-8 -*-
"""BFS 深爬引擎：整站爬取（替代 Crawl4AI 的 deep_crawling）。

流程：起始 URL 入队 → 抓取 → 提取数据 + 发现链接（同域/路径过滤）→ 入队
→ visited 去重 → 直到队列空或达到页数上限。

用法：
    from app.deep_crawler import DeepCrawler
    rows = DeepCrawler("https://site.com", max_pages=20).crawl(fields)
"""
from collections import deque
from urllib.parse import urljoin, urlparse

from .scraper import ScrapeError, fetch_page


def _filter_url(url: str, start_url: str, same_domain_only: bool,
                url_pattern: str) -> bool:
    """判断链接是否值得爬取。"""
    if not url or url.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return False
    url = url.split("#")[0]
    if not url.startswith("http"):
        return False
    if same_domain_only:
        base = urlparse(start_url).netloc
        if urlparse(url).netloc and urlparse(url).netloc != base:
            return False
    if url_pattern and url_pattern not in url:
        return False
    return True


class DeepCrawler:
    """BFS 整站爬虫。"""

    def __init__(self, start_url: str, max_pages: int = 15,
                 same_domain_only: bool = True, url_pattern: str = "",
                 progress: callable = None):
        self.start_url = start_url
        self.max_pages = max_pages
        self.same_domain_only = same_domain_only
        self.url_pattern = url_pattern
        self.progress = progress

    def _report(self, msg):
        if self.progress:
            self.progress(msg)

    def crawl(self, fields: list, fetcher: str = "static",
              wait_seconds: int = 2, proxy: str = "") -> list:
        """整站爬取，返回所有页面的数据行。

        自适应停止：连续 STALL_PAGES 页没有新增数据则提前结束。
        """
        queue = deque([self.start_url])
        visited = set()
        all_rows = []
        failed = 0
        seen_keys = set()          # 跨页去重用的主键集合
        stall_count = 0            # 连续无新增页数
        stall_pages = max(1, self.max_pages // 10)  # 连续 N 页无新数据就停
        key_name = fields[0].get("name", "") if fields else ""

        while queue and len(visited) < self.max_pages:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            self._report(f"深爬 {len(visited)}/{self.max_pages}: {url[:70]}")

            page_new = 0  # 本页新增行数
            try:
                page = fetch_page(url, fetcher, wait_seconds, proxy)
                status = getattr(page, "status", 0)

                # 提取数据（按主键字段对齐）
                columns = []
                for f in fields:
                    try:
                        els = page.css(f["selector"])
                    except Exception:
                        els = []
                    values = []
                    for el in els:
                        try:
                            if f.get("type") == "attr":
                                values.append(el.attrib.get(f.get("attr", ""), ""))
                            elif f.get("type") == "image":
                                values.append(el.attrib.get("src")
                                              or el.attrib.get("data-src") or "")
                            else:
                                values.append((el.text or "").strip())
                        except Exception:
                            values.append("")
                    columns.append((f.get("name", "字段"), values))
                lens = [len(v) for _, v in columns if v]
                if lens:
                    count = min(lens)
                    for i in range(count):
                        row = {}
                        for name, values in columns:
                            row[name] = values[i] if i < len(values) else ""
                        # 主键去重，统计新增
                        k = str(row.get(key_name, "")) if key_name else str(row)
                        if k and k in seen_keys:
                            continue
                        if k:
                            seen_keys.add(k)
                        all_rows.append(row)
                        page_new += 1
                    self._report(f"  ↳ 本页新增 {page_new} 行，累计 {len(all_rows)} 行")

                # 发现链接（同域/路径过滤）
                try:
                    links = page.css("a[href]")
                    for a in links:
                        href = a.attrib.get("href", "")
                        full = urljoin(url, href)
                        if _filter_url(full, self.start_url,
                                       self.same_domain_only,
                                       self.url_pattern):
                            queue.append(full)
                except Exception:
                    pass
            except ScrapeError as e:
                failed += 1
                self._report(f"  ⚠️ 页面失败：{e}")

            # 自适应停止：连续多页无新增数据
            stall_count = stall_count + 1 if page_new == 0 else 0
            if stall_count >= stall_pages:
                self._report(f"自适应停止：连续 {stall_count} 页无新增数据")
                break

        self._report(f"深爬完成：访问 {len(visited)} 页（失败 {failed}），"
                     f"共 {len(all_rows)} 行")
        return all_rows
