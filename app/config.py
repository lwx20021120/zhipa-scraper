# -*- coding: utf-8 -*-
"""全局配置读写（config.json 存于项目根目录）。"""
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG = {
    "api_key": "",          # DeepSeek API Key（P2 使用）
    "proxy": "",            # 代理地址，留空表示不用
    "default_wait": 2,      # 动态页默认等待秒数
    "max_pages": 5,         # 默认最大翻页数
}


def load_config() -> dict:
    """读取配置，缺失项用默认值补齐。"""
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in saved.items() if k in cfg})
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    """保存配置（仅保留已知键）。"""
    clean = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    CONFIG_FILE.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
