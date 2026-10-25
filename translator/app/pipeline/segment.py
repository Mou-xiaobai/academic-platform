# -*- coding: utf-8 -*-
"""把版面块切成翻译单元，并给出跳过原因（页眉/页码/参考文献/作者/公式…）。"""
from __future__ import annotations

import re

REF_HEADING = re.compile(r"^\s*(references?|bibliography|literature cited|参考文献|引用文献)\s*$", re.I)
PAGE_NUM = re.compile(r"^\s*(?:[-–—]\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*[-–—])?\s*$", re.I)
PAGE_NUM2 = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:/|of)\s*\d{1,4}\s*$", re.I)
URL_ONLY = re.compile(r"^(https?://|www\.|doi:|doi\.org|10\.\d{4,9}/)\S+$", re.I)
AFFIL = re.compile(
    r"\b(University|Universit|Department|Institute|College|School of|Laboratory|Academy|"
    r"Hospital|Faculty|Campus|Street|Road|Avenue|P\.?\s?O\.?\s?Box|E-mail|Corresponding author)\b",
    re.I,
)
CN_AFFIL = re.compile(r"(大学|学院|研究所|研究院|实验室|通信作者|通讯作者|邮箱)")


def _nchars(s: str) -> int:
    return len(s.strip())


def join_lines(lines: list) -> str:
    """把块内多行还原成一段文字（处理软连字符/硬连字符/普通断行）。"""
    if not lines:
        return ""
    out = lines[0]["text"].strip()
    for idx in range(1, len(lines)):
        prev = lines[idx - 1]["text"].rstrip()
        cur = lines[idx]["text"].strip()
        if not cur:
            continue
        if prev.endswith("\u00ad"):
            out = out[:-1] + cur
        elif prev.endswith("-") and len(prev) > 1 and (prev[-2].isalpha() or prev[-2].isdigit()):
            out = out + cur
        else:
            out = out + " " + cur
    out = re.sub(r"[ \t\u00a0]+", " ", out).strip()
    return out


def _letters(s: str) -> tuple[int, int]:
    latin = sum(1 for c in s if c.isalpha() and ord(c) < 0x2E80)
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    return latin, cjk


def _norm_band(text: str) -> str:
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_body_size(pages: list) -> float:
    from collections import Counter
    cnt = Counter()
    for p in pages:
        for b in p["blocks"]:
            for ln in b["lines"]:
                cnt[round(ln["size"] * 2) / 2] += len(ln["text"])
    if not cnt:
        return 10.0
    return float(cnt.most_common(1)[0][0])


def _bands(pages: list) -> set:
    """跨页重复的页眉/页脚文本（归一化后）。"""
    from collections import Counter
    seen = Counter()
    for p in pages:
        H = p["height"]
        for b in p["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if y0 < 0.075 * H or y1 > 0.925 * H:
                for ln in b["lines"]:
                    seen[_norm_band(ln["text"])] += 1
                continue
    n = len(pages)
    keep = set()
    for text, c in seen.items():
        if len(text) > 2 and c >= max(2, int(0.34 * n)):
            keep.add(text)
    return keep


def _refs_start(pages: list, body: float):
    for p in pages:
        for b in p["blocks"]:
            lines = b["lines"]
            if len(lines) > 2:
                continue
            text = join_lines(lines)
            if REF_HEADING.match(text) and len(text) <= 24 and lines[0]["size"] >= body * 0.85:
                return p["number"], b["bbox"][1], b["bbox"][0], b["bbox"][2], p["width"]
    return None


def _distinct_lines(block: dict) -> list:
    ys = {}
    for ln in block["lines"]:
        key = round(ln["bbox"][1], 1)
        ys.setdefault(key, ln)
    return [ys[k] for k in sorted(ys)]


def _alignment(block: dict) -> str:
    lines = _distinct_lines(block)
    if len(lines) < 2:
        return "left"
    x0, x1 = block["bbox"][0], block["bbox"][2]
    diffs, lefts = [], []
    for ln in lines:
        lg = ln["bbox"][0] - x0
        rg = x1 - ln["bbox"][2]
        diffs.append(abs(lg - rg))
        lefts.append(lg)
    diffs.sort()
    med = diffs[len(diffs) // 2]
    if med < 3.0 and min(lefts) > 4:
        return "center"
    return "left"


def _drop_overlaps(units: list, skipped: list) -> tuple:
    """同一页内互相重叠的块只保留面积较大的，避免译文叠印。"""
    by_page = {}
    for u in units:
        by_page.setdefault(u["page"], []).append(u)
    drop = set()
    for page, us in by_page.items():
        order = sorted(us, key=lambda u: (u["bbox"][2] - u["bbox"][0]) * (u["bbox"][3] - u["bbox"][1]),
                       reverse=True)
        kept = []
        for u in order:
            ux0, uy0, ux1, uy1 = u["bbox"]
            ua = max(1e-6, (ux1 - ux0) * (uy1 - uy0))
            hit = False
            for k in kept:
                kx0, ky0, kx1, ky1 = k["bbox"]
                ka = max(1e-6, (kx1 - kx0) * (ky1 - ky0))
                iw = min(ux1, kx1) - max(ux0, kx0)
                ih = min(uy1, ky1) - max(uy0, ky0)
                if iw > 0 and ih > 0 and (iw * ih) > 0.35 * min(ua, ka):
                    hit = True
                    break
            if hit:
                drop.add(u["id"])
                skipped.append({"id": u["id"], "page": page, "bbox": u["bbox"],
                                "text": u["text"][:160], "reason": "重叠块", "size": u["size"]})
            else:
                kept.append(u)
    return [u for u in units if u["id"] not in drop], skipped


def build_units(model: dict, opts: dict | None = None) -> dict:
    opts = opts or {}
    pages = model["pages"]
    body = detect_body_size(pages)
    bands = _bands(pages)
    refs = _refs_start(pages, body) if opts.get("skip_references", True) else None
    n_pages = len(pages)

    # 首页标题位置（用于跳过标题上方的刊头/出版商信息）
    title_y = None
    if pages:
        cand = [b for b in pages[0]["blocks"] if len(join_lines(b["lines"])) >= 12]
        if cand:
            biggest = max(max(l["size"] for l in b["lines"]) for b in cand)
            near = [b for b in cand if max(l["size"] for l in b["lines"]) >= biggest - 1.0]
            title = max(near, key=lambda b: len(join_lines(b["lines"])))
            title_y = title["bbox"][1]

    units, skipped = [], []
    for p in pages:
        pno = p["number"]
        H = p["height"]
        for b in p["blocks"]:
            text = join_lines(b["lines"])
            lines = b["lines"]
            size = max(l["size"] for l in lines)
            dlines = _distinct_lines(b)
            lead = 0.0
            if len(dlines) >= 2:
                lead = (dlines[-1]["bbox"][1] - dlines[0]["bbox"][1]) / (len(dlines) - 1)
            if lead <= 0:
                lead = size * 1.32

            def skip(reason):
                skipped.append({"id": b["id"], "page": pno, "bbox": b["bbox"],
                                "text": text[:160], "reason": reason,
                                "size": size})

            # ---- 1. 基本过滤
            if _nchars(text) < 2:
                skip("空白/符号"); continue
            if any(abs(l["dir"][0] - 1) > 0.01 or abs(l["dir"][1]) > 0.01 for l in lines):
                skip("旋转文字"); continue
            if b["bbox"][2] - b["bbox"][0] < 3 or b["bbox"][3] - b["bbox"][1] < 3:
                skip("过小区域"); continue

            # ---- 2. 页码 / 页眉页脚
            if PAGE_NUM.match(text) or PAGE_NUM2.match(text):
                skip("页码"); continue
            in_band = b["bbox"][1] < 0.075 * H or b["bbox"][3] > 0.925 * H
            if in_band and any(_norm_band(l["text"]) in bands for l in lines):
                skip("页眉/页脚"); continue

            # ---- 2.5 出版商首页信息 / 版权行
            if re.match(r"^\s*(contents lists available|journal homepage|available online|"
                        r"©|copyright|copyrighted)\b", text, re.I):
                skip("出版信息"); continue
            if re.match(r"^\s*(https?://doi\.org/|doi:)", text, re.I):
                skip("DOI"); continue
            if pno == 1 and title_y is not None and b["bbox"][3] <= title_y + 1:
                skip("刊头/首页信息"); continue
            if pno == 1 and b["bbox"][3] > 0.9 * H and re.search(
                    r"received|accepted|revised|available online|copyright|©|all rights reserved|"
                    r"Elsevier|Springer|Wiley|IEEE", text, re.I):
                skip("版权/收稿信息"); continue

            # ---- 3. 参考文献
            if refs:
                rp, ry, rx0, rx1, pw = refs
                cx = (b["bbox"][0] + b["bbox"][2]) / 2.0
                same_col = (cx >= rx0 - 25) and (cx <= rx1 + 25)
                after_in_col = same_col and b["bbox"][1] > ry + 1
                # 文献标题在左栏时，右栏整体都在其后
                cross_col = (cx > pw / 2.0) and ((rx0 + rx1) / 2.0 < pw / 2.0)
                if pno > rp or (pno == rp and (after_in_col or cross_col)):
                    skip("参考文献"); continue
            if opts.get("skip_references", True) and n_pages >= 4 and pno >= int(0.72 * n_pages):
                if re.match(r"^\[\d{1,3}\]", text):
                    skip("参考文献条目"); continue

            # ---- 4. 纯 URL / DOI / 邮箱
            if URL_ONLY.match(text):
                skip("URL/DOI"); continue
            if "@" in text and len(text) < 160 and re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
                skip("邮箱/通讯信息"); continue

            # ---- 5. 首页作者与单位
            if pno == 1 and b["bbox"][1] < 0.6 * H:
                if AFFIL.search(text) and size <= body * 1.25:
                    skip("作者单位"); continue
                if CN_AFFIL.search(text) and size <= body * 1.25:
                    skip("作者单位"); continue
                if re.search(r"Corresponding author|通信作者|通讯作者", text, re.I):
                    skip("通讯作者"); continue
                toks = re.findall(r"\b[A-Z][a-z]{1,}\b", text)
                if len(toks) >= 4 and text.count(",") >= 2 and not re.search(r"[.;:!?]", text):
                    skip("作者列表"); continue

            # ---- 6. 公式 / 无有效文字
            latin, cjk = _letters(text)
            letters = latin + cjk
            if letters / max(1, len(text)) < 0.28 and len(text) >= 8:
                skip("公式/符号"); continue
            if letters < 3:
                skip("无有效文字"); continue

            # ---- 7. 收集为翻译单元
            # 字号：按字符数加权的主字号（上标/下标不会把整段放大）
            wsum = {}
            for l in lines:
                k = l.get("size_dom", l["size"])
                wsum[k] = wsum.get(k, 0) + len(l["text"].strip())
            size_dom = max(wsum, key=wsum.get) if wsum else size
            n_chars = sum(len(l["text"].strip()) for l in lines) or 1
            bold_chars = sum(len(l["text"].strip()) for l in lines if l["bold"])
            italic_chars = sum(len(l["text"].strip()) for l in lines if l["italic"])
            bold = bold_chars / n_chars >= 0.6
            italic = italic_chars / n_chars >= 0.6
            if bold and len(dlines) <= 2:
                role = "heading"
            elif italic and len(dlines) == 1:
                role = "subheading"
            else:
                role = "body"

            # 基线锚定：用原文第一行基线，行距用相邻基线差的中位数
            oys = []
            for l in lines:
                o = l.get("origin")
                if not o:
                    continue
                if not oys or abs(o[1] - oys[-1]) > 0.5:
                    oys.append(o[1])
            if len(oys) >= 2:
                ds = sorted(oys[i + 1] - oys[i] for i in range(len(oys) - 1))
                lead_o = ds[len(ds) // 2]
            else:
                lead_o = size_dom * 1.32
            lead_o = min(max(lead_o, size_dom * 1.12), size_dom * 2.0)
            origin0 = lines[0].get("origin")
            indent = 0.0
            if origin0:
                indent = max(0.0, min(origin0[0] - b["bbox"][0],
                                       (b["bbox"][2] - b["bbox"][0]) * 0.35))
            units.append({
                "id": b["id"],
                "page": pno,
                "bbox": b["bbox"],
                "text": text,
                "lines": [l["bbox"] for l in lines],
                "style": role,
                "size": round(size_dom, 2),
                "lead": round(lead_o, 2),
                "baseline": origin0[1] if origin0 else None,
                "indent": round(indent, 2),
                "align": _alignment(b),
                "color": lines[0]["color"],
            })

    # 每个单元可用的下边界：不能压住同一栏下方任何块
    page_rects = {p["number"]: [b["bbox"] for b in p["blocks"]] for p in pages}
    for u in units:
        x0, y0, x1, y1 = u["bbox"]
        limit = y1 + 3 * u["size"]
        for rb in page_rects.get(u["page"], []):
            if rb == u["bbox"]:
                continue
            # 只考虑"起始位置在本块顶边之下"的块（可容忍二者 bbox 相交）
            if rb[1] < y0 + 0.5 or rb[3] < y0 + 2:
                continue
            overlap_x = min(x1, rb[2]) - max(x0, rb[0])
            if overlap_x < 0.25 * min(x1 - x0, rb[2] - rb[0]):
                continue
            limit = min(limit, rb[1] - 1.2)
        # 最多允许吃到自身底边下方 0.4pt，避免"恰好相邻"时无谓缩字
        u["limit_bottom"] = round(max(y1 - 0.4, limit), 2)

    units, skipped = _drop_overlaps(units, skipped)

    return {
        "units": units,
        "skipped": skipped,
        "body_size": body,
        "refs": refs,
        "stats": {
            "units": len(units),
            "chars": sum(len(u["text"]) for u in units),
            "skipped": len(skipped),
            "pages": n_pages,
            "body_size": body,
        },
    }