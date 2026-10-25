# -*- coding: utf-8 -*-
"""PDF 版面解析：页 → 文本块 → 行 → span，保留坐标/字号/字体/颜色。"""
from __future__ import annotations

import hashlib

import fitz


class ParseError(Exception):
    pass


def _round(v, nd=2):
    return round(float(v), nd)


def parse_pdf(path: str, max_pages: int = 200) -> dict:
    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"无法打开 PDF：{e}") from e

    if doc.needs_pass:
        if not doc.authenticate(""):
            raise ParseError("PDF 已加密，需要密码才能打开")
    if doc.page_count == 0:
        raise ParseError("PDF 没有任何页面")
    if doc.page_count > max_pages:
        raise ParseError(f"页数过多（{doc.page_count} 页），当前上限 {max_pages} 页")

    pages = []
    for pno in range(doc.page_count):
        page = doc[pno]
        d = page.get_text("dict")
        blocks = []
        bi = 0
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            lines = []
            for ln in b["lines"]:
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                size = max(s["size"] for s in spans)
                text = "".join(s["text"] for s in spans)
                flags = spans[0].get("flags", 0)
                buckets = {}
                for s in spans:
                    k = round(s["size"], 2)
                    buckets[k] = buckets.get(k, 0) + len(s["text"].strip())
                size_dom = max(buckets, key=buckets.get) if buckets else size
                origin = spans[0].get("origin", (ln["bbox"][0], ln["bbox"][3]))
                lines.append({
                    "bbox": [_round(v) for v in ln["bbox"]],
                    "text": text,
                    "size": _round(size),
                    "size_dom": _round(size_dom),
                    "origin": [_round(origin[0]), _round(origin[1])],
                    "font": spans[0]["font"],
                    "color": spans[0].get("color", 0),
                    "bold": bool(flags & 16) or any(
                        k in spans[0]["font"] for k in ("Bold", "Black", "Semibold", "Heavy")
                    ),
                    "italic": bool(flags & 2) or any(k in spans[0]["font"] for k in ("Italic", "Oblique")),
                    "dir": [_round(v, 3) for v in ln.get("dir", (1, 0))],
                })
            if not lines:
                continue
            blocks.append({
                "id": f"p{pno + 1}b{bi}",
                "bbox": [_round(v) for v in b["bbox"]],
                "lines": lines,
            })
            bi += 1

        images = []
        for info in page.get_image_info():
            images.append({"bbox": [_round(v) for v in info["bbox"]],
                           "w": info.get("width"), "h": info.get("height")})

        pages.append({
            "number": pno + 1,
            "width": _round(page.rect.width),
            "height": _round(page.rect.height),
            "rotation": page.rotation,
            "blocks": blocks,
            "images": images,
        })

    meta = doc.metadata or {}
    text_len = sum(len(l["text"]) for p in pages for b in p["blocks"] for l in b["lines"])
    model = {
        "title": (meta.get("title") or "").strip(),
        "author": (meta.get("author") or "").strip(),
        "subject": (meta.get("subject") or "").strip(),
        "page_count": len(pages),
        "pages": pages,
        "text_chars": text_len,
        "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
    }
    if text_len < 40:
        raise ParseError("没有提取到文本层，可能是扫描件（本工具暂不支持 OCR）")
    return model
