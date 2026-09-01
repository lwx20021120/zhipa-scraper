# -*- coding: utf-8 -*-
"""内容去噪：借鉴 Crawl4AI 的 PruningContentFilter 思想。

原理：正文区块（p/h/li/article/section）保留，噪声标签（nav/footer/header/
aside/script/style/广告 class）剔除，按"文本长度"过滤碎片。
用于 AI 直提模式前，让喂给 LLM 的文本更干净、token 更省。
"""
from lxml import html

# 直接剔除的标签（导航/页脚/脚本/广告）
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside",
              "noscript", "iframe", "form"]
# 按 class 特征剔除（常见广告/导航 class 片段）
NOISE_CLASS_MARKS = ("advert", "banner", "nav", "menu", "footer",
                     "header", "sidebar", "widget", "comment", "share",
                     "recommend", "related", "popup", "modal", "cookie")
# 正文类标签（权重高）
CONTENT_TAGS = ("p", "h1", "h2", "h3", "h4", "li", "td", "article",
                "section", "blockquote", "pre")
# 低于该字符数的碎片视为噪声
MIN_TEXT_LEN = 20


def _is_noise_class(class_attr: str) -> bool:
    c = (class_attr or "").lower()
    return any(mark in c for mark in NOISE_CLASS_MARKS)


def extract_clean_text(html_text: str, max_chars: int = 6000) -> str:
    """从 HTML 提取干净正文文本（去导航/广告/脚本）。

    参数：
        html_text: 原始 HTML
        max_chars: 输出最大字符数
    返回：去噪后的正文文本。
    """
    if not html_text:
        return ""
    if isinstance(html_text, bytes):
        html_text = html_text.decode("utf-8", "ignore")

    try:
        doc = html.fromstring(html_text)
    except Exception:
        return html_text[:max_chars]

    # 1. 剔除噪声标签
    for el in doc.cssselect(", ".join(NOISE_TAGS)):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)

    # 2. 剔除广告/导航 class 的容器（尽量只删浅层节点，避免误删正文）
    for el in doc.iter():
        cls = el.get("class")
        if cls and _is_noise_class(cls):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    # 3. 收集正文标签文本
    body = doc.body
    if body is None:
        return html_text[:max_chars]

    parts = []
    for el in body.iter(*CONTENT_TAGS):
        txt = (el.text_content() or "").strip()
        if len(txt) >= MIN_TEXT_LEN:
            parts.append(txt)

    # 4. 无正文时退回整体文本
    if not parts:
        parts = [(body.text_content() or "").strip()]

    joined = "\n".join(parts)
    return joined[:max_chars]
