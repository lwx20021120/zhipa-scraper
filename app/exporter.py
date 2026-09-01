# -*- coding: utf-8 -*-
"""数据导出：rows -> pandas DataFrame -> CSV / Excel / JSON；图片批量下载。"""
import urllib.request
from pathlib import Path

import pandas as pd

DEFAULT_OUTPUT = "抓取结果"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")


def to_dataframe(rows: list) -> "pd.DataFrame":
    """rows(list[dict]) 转 DataFrame，空数据给空表。"""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def export_csv(rows: list, path: str) -> None:
    """导出 CSV（utf-8-sig，Excel 打开不乱码）。"""
    df = to_dataframe(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def export_excel(rows: list, path: str) -> None:
    """导出 Excel（需要 openpyxl）。"""
    df = to_dataframe(rows)
    df.to_excel(path, index=False)


def export_json(rows: list, path: str) -> None:
    """导出 JSON（数组格式，保留中文）。"""
    df = to_dataframe(rows)
    df.to_json(path, orient="records", force_ascii=False, indent=2)


def collect_image_urls(rows: list, fields: list) -> list:
    """从抓取结果中收集图片字段的 URL（type=image 或字段名含图/图片/封面）。"""
    img_fields = [f["name"] for f in fields if f.get("type") == "image"]
    if not img_fields:
        img_fields = [n for n in (rows[0].keys() if rows else [])
                      if any(k in n for k in ("图", "图片", "封面", "image", "img"))]
    urls = []
    for r in rows:
        for name in img_fields:
            v = str(r.get(name, "")).strip()
            if v and v not in urls and v.lower().endswith(IMAGE_EXTS):
                urls.append(v)
    return urls


def download_images(urls: list, save_dir: str) -> tuple:
    """批量下载图片到文件夹。返回 (成功数, 失败数)。"""
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for i, url in enumerate(urls, 1):
        if not url:
            fail += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=15).read()
            ext = Path(url.split("?")[0]).suffix.lower()
            if ext not in IMAGE_EXTS:
                ext = ".jpg"
            Path(save_dir, f"图片_{i:04d}{ext}").write_bytes(data)
            ok += 1
        except Exception:
            fail += 1
    return ok, fail
