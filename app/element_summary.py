# -*- coding: utf-8 -*-
"""页面元素清单：从 HTML 提取"有意义元素"的 tag+class+文本，给 AI 参考写 selector。

让 AI 不再盲猜，而是看真实页面结构（字段名/类名/标签）生成精准 selector。
"""
from lxml import html as lxml_html

IGNORED_TAGS = {"html", "body", "head", "meta", "link", "script",
                "style", "noscript", "br", "hr"}


def extract_element_summary(html_text: str, max_items: int = 80,
                            max_chars: int = 2000) -> str:
    """从 HTML 提取元素清单（tag.class: 文本片段），给 AI 参考。

    例：
        ol > li.grid_view_item:
          h3.title: A Light in the Attic
          p.price_color: £51.77
        ...

    返回去重后按层级排序的清单。
    """
    if not html_text:
        return ""
    if isinstance(html_text, bytes):
        html_text = html_text.decode("utf-8", "ignore")
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return ""

    items = []
    seen = set()
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if tag in IGNORED_TAGS:
            continue
        cls = el.get("class")
        text = (el.text or "").strip()
        if not cls and not text:
            continue
        # 标签 + class（紧凑显示）
        desc = f"{tag}"
        if cls:
            desc += f".{cls[:40].replace(chr(32), '.')}"
        if text:
            desc += f": {text[:30]}"
        if desc in seen:
            continue
        seen.add(desc)
        items.append(desc)
        if len(items) >= max_items:
            break
    summary = " / ".join(items)
    return summary[:max_chars]