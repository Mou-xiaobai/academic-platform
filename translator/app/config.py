# -*- coding: utf-8 -*-
"""全局配置：路径、字体发现、API Key 发现、可调参数。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOBS = DATA / "jobs"
UPLOADS = DATA / "uploads"
STATIC = ROOT / "app" / "static"
SETTINGS_FILE = DATA / "settings.json"

for _p in (DATA, JOBS, UPLOADS):
    _p.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "model": "deepseek-chat",
    "model_quality": "deepseek-chat",
    "concurrency": 4,
    "batch_chars": 2600,
    "max_pages": 80,
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "glossary": "default",
    "translate_captions": True,
    "skip_references": True,
    "font_body": "",
    "font_heading": "",
    "font_italic": "",
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(patch: dict) -> dict:
    s = load_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def _key_from_credentials() -> str:
    p = Path.home() / ".dsh" / ".credentials.yaml"
    if p.exists():
        try:
            m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", p.read_text(encoding="utf-8"))
            if m:
                return m.group(1).strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def api_key(settings: dict | None = None) -> str:
    s = settings or load_settings()
    return (s.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or _key_from_credentials()).strip()


# ---------------------------------------------------------------- fonts
FONT_CANDIDATES = {
    "body": [
        ("C:/Windows/Fonts/STSONG.TTF", "宋体"),
        ("C:/Windows/Fonts/simsun.ttc", "宋体"),
        ("C:/Windows/Fonts/NotoSerifSC-Regular.otf", "思源宋体"),
        ("C:/Windows/Fonts/msyh.ttc", "雅黑"),
    ],
    "heading": [
        ("C:/Windows/Fonts/simhei.ttf", "黑体"),
        ("C:/Windows/Fonts/msyhbd.ttc", "雅黑粗"),
        ("C:/Windows/Fonts/STZHONGS.TTF", "华文中宋"),
    ],
    "italic": [
        ("C:/Windows/Fonts/simkai.ttf", "楷体"),
        ("C:/Windows/Fonts/simfang.ttf", "仿宋"),
        ("C:/Windows/Fonts/STKAITI.TTF", "华文楷体"),
    ],
}

LATIN_CANDIDATES = {
    "body": ["C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/georgia.ttf"],
    "heading": ["C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/georgiab.ttf"],
    "italic": ["C:/Windows/Fonts/timesi.ttf", "C:/Windows/Fonts/georgiai.ttf"],
}
