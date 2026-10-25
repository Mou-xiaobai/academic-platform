# -*- coding: utf-8 -*-
"""版式重建：抹除原文 → 在原 bbox 内重绘译文 → 按需子集化字体。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import fitz

from app.pipeline import cjk
from app.pipeline.fonts import build_subset_fonts


def _rgb(color: int):
    if not color:
        return (0.0, 0.0, 0.0)
    return (((color >> 16) & 0xFF) / 255.0, ((color >> 8) & 0xFF) / 255.0, (color & 0xFF) / 255.0)


def _clip_against(r, protected) -> fitz.Rect:
    """把 redaction 矩形从"未翻译块"的文字矩形中裁开，避免误擦相邻文本。"""
    for p in protected:
        if r.is_empty:
            break
        inter = r & p
        if inter.is_empty or inter.get_area() < 0.02:
            continue
        if inter.y0 <= r.y0 + 0.7 and inter.y1 < r.y1 - 0.7:
            r = fitz.Rect(r.x0, max(r.y0, inter.y1), r.x1, r.y1)
        elif inter.y1 >= r.y1 - 0.7 and inter.y0 > r.y0 + 0.7:
            r = fitz.Rect(r.x0, r.y0, r.x1, min(r.y1, inter.y0))
        elif inter.x0 <= r.x0 + 0.7 and inter.x1 < r.x1 - 0.7:
            r = fitz.Rect(max(r.x0, inter.x1), r.y0, r.x1, r.y1)
        elif inter.x1 >= r.x1 - 0.7 and inter.x0 > r.x0 + 0.7:
            r = fitz.Rect(r.x0, r.y0, min(r.x1, inter.x0), r.y1)
    return r


def rebuild(src_path: str, units: list, translations: dict, out_path: str,
            lang: str = "zh", meta: dict | None = None, font_dir: str | None = None,
            protected_rects: list | None = None) -> dict:
    doc = fitz.open(src_path)
    prot_by_page = {int(k): [fitz.Rect(x) for x in v]
                    for k, v in (protected_rects or {}).items()}
    protected = []
    by_page = defaultdict(list)
    used_chars = []
    for u in units:
        tr = translations.get(u["id"])
        if isinstance(tr, str) and tr.strip():
            by_page[u["page"]].append(u)
            used_chars.append(tr)

    fdir = Path(font_dir or (Path(out_path).parent / "fonts"))
    fonts = build_subset_fonts("".join(used_chars), fdir, lang=lang)

    report = []
    for pno in sorted(by_page):
        if pno < 1 or pno > doc.page_count:
            continue
        page = doc[pno - 1]
        page_units = by_page[pno]
        protected = prot_by_page.get(pno, [])
        for u in page_units:
            for lb in u["lines"]:
                rr = fitz.Rect(lb[0] - 0.6, lb[1] - 0.35, lb[2] + 0.6, lb[3] + 0.35)
                if protected:
                    rr = _clip_against(rr, protected)
                if rr.is_empty or rr.height < 1.2 or rr.width < 1.2:
                    continue
                page.add_redact_annot(rr, fill=False)
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        except TypeError:
            page.apply_redactions()

        tw = fitz.TextWriter(page.rect, color=(0, 0, 0))
        for u in page_units:
            txt = translations[u["id"]].strip()
            font = fonts.get(u.get("style") or "body", fonts["body"])[0]
            res = cjk.layout(txt, u["bbox"], font, u["size"], u["lead"],
                             align=u.get("align", "left"),
                             baseline0=u.get("baseline"),
                             first_indent=u.get("indent") or 0.0,
                             bottom_limit=u.get("limit_bottom"))
            for baseline, chunks in res["lines"]:
                for x, piece in chunks:
                    tw.append(fitz.Point(x, baseline), piece, font=font, fontsize=res["size"])
            box_h = float(u["bbox"][3]) - float(u["bbox"][1])
            over = max(0.0, res["bottom"] - (u.get("limit_bottom") or u["bbox"][3]))
            report.append({
                "id": u["id"], "page": pno, "size0": u["size"], "size": res["size"],
                "lines": len(res["lines"]), "box_h": round(box_h, 1),
                "height": res["height"], "bottom": res["bottom"],
                "overflow": round(over, 2),
                "shrunk": res["size"] < u["size"] - 0.01,
            })
        tw.write_text(page)

    if meta:
        try:
            md = doc.metadata or {}
            md.update({
                "title": meta.get("title") or md.get("title") or "",
                "subject": meta.get("subject") or md.get("subject") or "",
                "keywords": meta.get("keywords") or md.get("keywords") or "",
            })
            doc.set_metadata(md)
        except Exception:
            pass

    doc.save(out_path, garbage=3, deflate=True)
    doc.close()

    return {
        "units": len(report),
        "shrunk": sum(1 for r in report if r["shrunk"]),
        "overflow": sum(1 for r in report if r["overflow"] > 2.0),
        "max_overflow": max([r["overflow"] for r in report], default=0.0),
        "report": report,
    }
