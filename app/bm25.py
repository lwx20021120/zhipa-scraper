# -*- coding: utf-8 -*-
"""BM25 相关性过滤：按用户需求从文本块中找最相关内容（借鉴 Crawl4AI）。

场景：direct_extract 顺序取块时，正文在页面中后部的页面会漏内容。
BM25 按查询词打分选出最相关的块，只喂给 LLM，提取更准、token 更省。

中文简化方案：查询词 = 用户输入去停用词后的词；块与查询词做 BM25 打分。
"""
import math
import re

# 常见无意义词（爬虫指令类）
STOPWORDS = {
    "爬", "的", "这个", "页面", "所有", "提取", "数据", "我要", "我要的",
    "和", "以及", "下来", "网站", "列表", "内容", "信息", "请", "帮我",
    "抓取", "采集", "整理", "导出", "那个", "这些", "这里", "然后", "还有",
}

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]{2,}")


def tokenize(text: str) -> list:
    """简易分词（无词典）：中文按相邻二元组 bigram，英文/数字按词。

    例："提取商品名称和价格" → ['提取','取商','商品','品名','名称','称和','和价','价格']
    """
    tokens = []
    for chunk in TOKEN_RE.findall(text):
        if chunk.isascii():
            w = chunk.lower()
            if w not in STOPWORDS:
                tokens.append(w)
        elif len(chunk) >= 2:
            # 中文 bigram（含与原 chunk 的匹配，保留更长的词）
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
            # 也保留完整中文词（可能匹配到块内完整词）
            tokens.append(chunk)
    return [t for t in tokens if t not in STOPWORDS]


def _df(blocks: list, term: str) -> int:
    """包含词项的块数（文档频率）。"""
    return sum(1 for b in blocks if term in b)


def bm25_rank(query: str, blocks: list, top_k: int = 3) -> list:
    """按 BM25 对文本块打分，返回最相关的 top_k 块。

    参数：
        query: 用户需求（如"提取商品价格和标题"）
        blocks: 文本块列表
        top_k: 返回最相关块数
    返回：按分数从高到低排列的相关块列表。
    """
    q_terms = tokenize(query)
    if not q_terms:
        return blocks[:top_k]  # 无法分词则顺序取

    n = len(blocks)
    avgdl = sum(len(b) for b in blocks) / max(n, 1)
    k1, b_param = 1.5, 0.75

    def score(block: str) -> float:
        doc_len = len(block)
        total = 0.0
        for term in q_terms:
            tf = block.lower().count(term)
            if tf == 0:
                continue
            df = _df(blocks, term)
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
            total += idf * (tf * (k1 + 1)) / (
                tf + k1 * (1 - b_param + b_param * doc_len / max(avgdl, 1)))
        return total

    scored = [(score(b), b) for b in blocks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:top_k]]
