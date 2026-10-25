# -*- coding: utf-8 -*-
"""成品质检：文字压图、溢出版块、空译、页数一致性。"""
from __future__ import annotations

import fitz


def check(out_path: str, src_path: str, model: dict, rebuild_stats: dict,
          translations: dict, units: list) -> dict:
    try:
        doc = fitz.open(out_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "无法打开产出 PDF：%s" % e}

    img_rects = {}
    for p in model["pages"]:
        img_rects[p["number"]] = [fitz.Rect(i["bbox"]) for i in p.get("images", [])]

    over_image = 0
    for pno in range(1, doc.page_count + 1):
        page = doc[pno - 1]
        imgs = img_rects.get(pno, [])
        if not imgs:
            continue
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            r = fitz.Rect(b["bbox"])
            area = max(1e-6, r.get_area())
            for im in imgs:
                inter = r & im
                if not inter.is_empty and inter.get_area() / area > 0.15:
                    over_image += 1
                    break

    empty = sum(1 for u in units if not (translations.get(u["id"]) or "").strip())
    done = len(units) - empty
    stats = {
        "ok": True,
        "pages": doc.page_count,
        "pages_src": model["page_count"],
        "page_match": doc.page_count == model["page_count"],
        "units": len(units),
        "translated": done,
        "empty": empty,
        "over_image": over_image,
        "overflow": rebuild_stats.get("overflow", 0),
        "shrunk": rebuild_stats.get("shrunk", 0),
        "max_overflow": rebuild_stats.get("max_overflow", 0.0),
    }
    doc.close()
    return stats
