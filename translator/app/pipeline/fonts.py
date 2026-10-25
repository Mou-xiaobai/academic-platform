# -*- coding: utf-8 -*-
"""中文字体发现 + 按需子集化（只嵌入用到的字符，避免调用 doc.subset_fonts 破坏原字体）。"""
from __future__ import annotations

import threading
from pathlib import Path

import fitz

from app import config

_LOCK = threading.Lock()
_CACHE: dict[str, tuple] = {}

EXTRA_CHARS = (
    "，。、；：？！（）【】《》「」『』“”‘’％·…—～－ "
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "+-−=×÷±<>≤≥%/\\|@#&*()[]{}'\"_~^:;,.!? "
    "αβγδεζηθικλμνξοπρστυφχψωΔΩμΩÅ°℃²³⁻¹"
)


def _try_open(path: str, probe: int = 0x4E2D):
    try:
        f = fitz.Font(fontfile=path)
        if probe == 0 or f.has_glyph(probe):
            return f
    except Exception:
        return None
    return None


def _pick(group: dict, role: str, override: str | None, probe: int = 0x4E2D):
    if override:
        f = _try_open(override, probe)
        if f is not None:
            return f, override
    for path, _name in group[role]:
        f = _try_open(path, probe)
        if f is not None:
            return f, path
    return None, None


def get_role_fonts(lang: str = "zh") -> dict:
    """{role: (fitz.Font, path)} —— 整字体（未子集），仅在无字体文件时作为后备。"""
    key = "latin" if lang.lower().startswith("en") else "zh"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        s = config.load_settings()
        if key == "zh":
            groups = config.FONT_CANDIDATES
            overrides = {"body": s.get("font_body"), "heading": s.get("font_heading"),
                         "italic": s.get("font_italic")}
        else:
            groups = {k: [(p, k) for p in v] for k, v in config.LATIN_CANDIDATES.items()}
            overrides = {}
        probe = 0x4E2D if key == "zh" else 0x41
        out = {}
        for role in ("body", "heading", "italic"):
            f, path = _pick(groups, role, overrides.get(role), probe)
            if f is None:
                f = fitz.Font("china-s") if key == "zh" else fitz.Font("tiro")
                path = ""
            out[role] = (f, path)
        _CACHE[key] = out
        return out


def build_subset_fonts(chars: str, out_dir: Path, lang: str = "zh") -> dict:
    """把用到的字符子集化到 out_dir，返回 {role: (fitz.Font, path)}。"""
    base_fonts = get_role_fonts(lang)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = set(chars) | set(EXTRA_CHARS)
    result = {}
    for role, (font, path) in base_fonts.items():
        if not path:
            result[role] = (font, path)
            continue
        dst = out_dir / ("%s_subset%s" % (role, Path(path).suffix.lower()))
        try:
            if not dst.exists():
                _subset_font(path, keep, dst)
            result[role] = (fitz.Font(fontfile=str(dst)), str(dst))
        except Exception:
            result[role] = (font, path)
    return result


def _subset_font(src: str, chars: set, dst: Path):
    from fontTools import subset as ft

    opts = ft.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.drop_tables += ["DSIG"]
    font = ft.load_font(src, opts)
    sub = ft.Subsetter(options=opts)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    ft.save_font(font, str(tmp), opts)
    tmp.replace(dst)


def clear_cache():
    with _LOCK:
        _CACHE.clear()
