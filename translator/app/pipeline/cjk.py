# -*- coding: utf-8 -*-
"""中/英混排断行、两端对齐、按框自适应字号——版式重建的排版引擎。

v2：基线锚定（按原文 baseline 落字，避免标题/正文下移挤在一起）、
首行缩进、下方安全边界（可以占用块下空白，但不压住下一块）。
"""
from __future__ import annotations

import re

CJK_BLOCK = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
CJK_RE = re.compile("[" + CJK_BLOCK + "]")
FULL_PUNCT = "，。、；：？！（）【】《》「」『』“”‘’％·…—"
FORBID_START = set("，。、；：？！）】》」』’”％·…,.;:?!)]}>%")
FORBID_END = set("（【《「『‘“([<{")


def normalize(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z0-9)\]])／(?=[A-Za-z0-9(\[])", "/", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return text.strip()


def atomize(text: str):
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == " ":
            j = i
            while j < n and text[j] == " ":
                j += 1
            out.append(("sp", text[i:j]))
            i = j
        elif CJK_RE.match(ch) and ch not in FULL_PUNCT:
            out.append(("cjk", ch))
            i += 1
        elif ch in FULL_PUNCT:
            out.append(("full", ch))
            i += 1
        else:
            j = i
            while j < n and not CJK_RE.match(text[j]) and text[j] != " ":
                j += 1
            out.append(("w", text[i:j]))
            i = j
    return out


def wrap(text: str, width: float, size: float, font, first_width: float | None = None) -> list:
    text = normalize(text)
    atoms = atomize(text)
    widths = [font.text_length(a[1], fontsize=size) for a in atoms]
    lines, cur, curw = [], [], 0.0
    limit = first_width if (first_width and first_width > width * 0.3) else width
    for idx, (kind, s) in enumerate(atoms):
        w = widths[idx]
        if kind == "sp":
            if not cur:
                continue
            if curw + w > limit:
                lines.append(cur)
                cur, curw = [], 0.0
                limit = width
                continue
            cur.append(idx)
            curw += w
            continue
        if cur and curw + w > limit:
            if s in FORBID_START and len(cur) > 1:
                moved = cur.pop()
                lines.append(cur)
                cur = [moved, idx]
                curw = widths[moved] + w
                limit = width
                continue
            if atoms[cur[-1]][1] in FORBID_END and len(cur) > 1:
                moved = cur.pop()
                lines.append(cur)
                cur = [moved, idx]
                curw = widths[moved] + w
                limit = width
                continue
            lines.append(cur)
            cur, curw = [idx], w
            limit = width
        else:
            cur.append(idx)
            curw += w
    if cur:
        lines.append(cur)
    if len(lines) >= 2:
        last = lines[-1]
        if len(last) == 1 and atoms[last[0]][0] == "cjk":
            prev = lines[-2]
            if len(prev) > 2 and atoms[prev[-1]][0] == "cjk":
                lines[-1] = [prev.pop()] + lines[-1]
    return [[atoms[i] for i in ln] for ln in lines]


def layout(text: str, box, font, size0: float, lead0: float, align: str = "left",
           justify: bool = True, min_size: float = 4.6, max_grow: float = 1.0,
           baseline0: float | None = None, first_indent: float = 0.0,
           bottom_limit: float | None = None):
    x0, y0, x1, y1 = [float(v) for v in box]
    w = max(8.0, x1 - x0)
    bottom = max(y1, float(bottom_limit)) if bottom_limit else y1
    size = float(size0)
    lead = float(lead0) if lead0 else size0 * 1.32
    min_size = min(min_size, size)

    try:
        cjk_font = bool(font.has_glyph(0x4E2D))
    except Exception:
        cjk_font = False
    asc_f, desc_f = (0.88, 0.12) if cjk_font else (0.76, 0.22)
    indent = max(0.0, min(float(first_indent or 0.0), w * 0.4))

    def ink(s):
        return (asc_f + desc_f) * s

    def wrap_at(s):
        return wrap(text, w, s, font, w - indent)

    def fits(s, l, n):
        if baseline0 is not None:
            top = baseline0 - asc_f * s
            bot = baseline0 + (n - 1) * l + desc_f * s
            return top >= y0 - 2.0 and bot <= bottom + 0.6
        return (n - 1) * l + ink(s) <= (bottom - y0) + 0.8

    lines = wrap_at(size)
    guard = 0
    while not fits(size, lead, len(lines)) and size > min_size and guard < 320:
        prev = size
        size = round(size - 0.15, 2)
        lead = lead * (size / prev)
        lines = wrap_at(size)
        guard += 1
    if size > size0 * max_grow:
        size = size0 * max_grow
        lines = wrap_at(size)

    n = len(lines)
    if n >= 2:
        if baseline0 is not None:
            room = (bottom - desc_f * size - baseline0) / (n - 1)
        else:
            room = ((bottom - y0) - ink(size)) / (n - 1)
        lead = max(lead, min(lead * 1.18, room))

    if baseline0 is not None:
        first = float(baseline0)
    elif n == 1:
        first = y0 + ((bottom - y0) - ink(size)) / 2.0 + asc_f * size
    else:
        first = y0 + asc_f * size

    out = []
    for i, ln in enumerate(lines):
        baseline = first + i * lead
        widths = [font.text_length(a[1], fontsize=size) for a in ln]
        total = sum(widths)
        line_w = (w - indent) if i == 0 else w
        n_atoms = sum(1 for a in ln if a[0] != "sp")
        n_gaps = max(1, n_atoms - 1)
        extra = 0.0
        if justify and i < n - 1 and total < line_w:
            cap = 2.2 if any(a[0] in ("cjk", "full") for a in ln) else 3.6
            e = (line_w - total) / n_gaps
            if e <= cap:
                extra = e
        base_x = x0 + (indent if i == 0 else 0.0)
        start = base_x
        if align == "center":
            start = base_x + (line_w - (total + extra * n_gaps)) / 2.0
        elif align == "right":
            start = base_x + (line_w - (total + extra * n_gaps))
        x = start
        chunks = []
        last_idx = max((k for k, a in enumerate(ln) if a[0] != "sp"), default=-1)
        for k, (a, aw) in enumerate(zip(ln, widths)):
            if a[0] == "sp":
                x += aw
                continue
            chunks.append((x, a[1]))
            x += aw + (extra if k < last_idx else 0.0)
        out.append((baseline, chunks))

    last_baseline = first + (n - 1) * lead
    return {
        "size": round(size, 2),
        "lead": round(lead, 2),
        "lines": out,
        "height": round(last_baseline + desc_f * size - y0, 2),
        "bottom": round(last_baseline + desc_f * size, 2),
        "limit": round(bottom, 2),
    }
