# -*- coding: utf-8 -*-
"""任务编排：解析 → 分块 → 翻译 → 重建 → 质检。"""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz

from app import config, store
from app.pipeline import glossary as gl_mod
from app.pipeline import parse as parse_mod
from app.pipeline import qa as qa_mod
from app.pipeline import rebuild as rb_mod
from app.pipeline import segment as seg_mod
from app.pipeline.translate import Translator

_executor = ThreadPoolExecutor(max_workers=1)
_lock = threading.Lock()
_running: set = set()

STAGE_LABELS = {
    "queued": "排队中",
    "parse": "解析版面",
    "segment": "切分翻译单元",
    "translate": "翻译中",
    "rebuild": "重排版面",
    "qa": "质量检查",
    "done": "完成",
    "error": "出错",
}


def _save(job: dict):
    job["updated_at"] = time.time()
    try:
        with _lock:
            store.save(job)
    except Exception:
        pass


def _stage(job: dict, stage: str, progress: float, message: str = ""):
    job["stage"] = stage
    job["stage_label"] = STAGE_LABELS.get(stage, stage)
    job["progress"] = round(max(0.0, min(100.0, progress)), 1)
    if message:
        job["message"] = message
        log = job.setdefault("log", [])
        log.append({"t": time.time(), "text": message})
        job["log"] = log[-80:]
    _save(job)


def submit(job_id: str):
    _executor.submit(_run, job_id)


def _run(job_id: str):
    job = store.load(job_id)
    if not job:
        return
    _running.add(job_id)
    t0 = time.time()
    job["status"] = "running"
    _save(job)
    try:
        settings = config.load_settings()
        jdir = store.job_dir(job_id)
        src = jdir / "source.pdf"
        out = jdir / "out.pdf"

        _stage(job, "parse", 3, "正在解析 PDF 版面…")
        model = parse_mod.parse_pdf(str(src), max_pages=int(settings.get("max_pages") or 80))
        (jdir / "layout.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        job["pages"] = model["page_count"]
        job["chars_src"] = model["text_chars"]
        job["doc_title"] = model["title"]
        _save(job)

        _stage(job, "segment", 8, "正在切分翻译单元…")
        opts = {
            "skip_references": bool(job["opts"].get("skip_references", True)),
        }
        seg = seg_mod.build_units(model, opts)
        units = seg["units"]
        (jdir / "units.json").write_text(json.dumps(seg, ensure_ascii=False), encoding="utf-8")
        job["units"] = seg["stats"]["units"]
        job["chars"] = seg["stats"]["chars"]
        job["skipped"] = seg["stats"]["skipped"]
        _save(job)

        if not units:
            raise RuntimeError("没有找到可翻译的文本块（可能是扫描件或纯图片 PDF）")

        _stage(job, "translate", 12, "翻译 %d 个文本块（%d 字符）…" % (len(units), seg["stats"]["chars"]))
        tcfg = dict(settings)
        tcfg["_cache_path"] = config.DATA / "cache.json"
        if job["opts"].get("model"):
            tcfg["model"] = job["opts"]["model"]
        key = config.api_key(settings)
        if not key:
            raise RuntimeError("未配置 API Key，请在「设置」里填写 DEEPSEEK_API_KEY")
        translator = Translator(tcfg, key)

        def on_progress(done_batches, total_batches, total_chars, done_chars, stats):
            ratio = (done_chars / total_chars) if total_chars else 1.0
            p = 12 + 68 * min(1.0, ratio)
            _stage(job, "translate", p,
                   "已翻译 %d/%d 批（约 %d%%）" % (done_batches, total_batches, int(ratio * 100)))
            job["tokens"] = {"in": stats.get("tokens_in", 0), "out": stats.get("tokens_out", 0),
                             "calls": stats.get("calls", 0), "cache_hits": stats.get("cache_hits", 0)}
            _save(job)

        src_lang = job["opts"].get("src_lang") or "en"
        tgt_lang = job["opts"].get("tgt_lang") or "zh"
        preset = job["opts"].get("glossary") or "default"
        custom = job["opts"].get("glossary_custom") or {}
        terms = dict(gl_mod.PRESETS.get(preset, {}))
        terms.update(custom)

        translations = translator.translate_units(units, src_lang, tgt_lang, terms,
                                                  on_progress=on_progress)
        (jdir / "translations.json").write_text(
            json.dumps(translations, ensure_ascii=False, indent=0), encoding="utf-8")
        missing = [u["id"] for u in units if not (translations.get(u["id"]) or "").strip()]
        if len(missing) == len(units):
            raise RuntimeError("全部文本块翻译失败，请检查 API Key / 网络 / 余额")

        _stage(job, "rebuild", 82, "正在抹除原文并重绘中文版面…")
        meta = {
            "title": (translations.get(units[0]["id"]) if units and units[0].get("style") == "heading" else "") or model["title"],
            "subject": "机器翻译（版式保真）：%s" % (model["title"] or job["filename"]),
            "keywords": "机器翻译; 版式保真; " + (model["subject"] or ""),
        }
        tids = set(translations)
        protected = {}
        for p in model["pages"]:
            rects = []
            for b in p["blocks"]:
                if b["id"] not in tids:
                    rects.extend([l["bbox"] for l in b["lines"]])
            if rects:
                protected[p["number"]] = rects
        rb = rb_mod.rebuild(str(src), units, translations, str(out), lang=tgt_lang, meta=meta,
                            protected_rects=protected)

        _stage(job, "qa", 94, "正在做质量检查…")
        qa = qa_mod.check(str(out), str(src), model, rb, translations, units)

        blocks = []
        for u in units:
            blocks.append({
                "id": u["id"], "page": u["page"], "bbox": u["bbox"], "text": u["text"],
                "zh": translations.get(u["id"], ""), "style": u["style"],
                "size": u["size"], "skipped": False,
            })
        payload = {"blocks": blocks, "skipped": seg["skipped"], "pages": model["page_count"],
                   "page_sizes": [[p["width"], p["height"]] for p in model["pages"]]}
        (jdir / "blocks.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        job["qa"] = qa
        job["stats"] = {"rebuild": {k: rb[k] for k in ("units", "shrunk", "overflow", "max_overflow")}}
        job["missing"] = len(missing)
        job["duration"] = round(time.time() - t0, 1)
        job["out"] = "out.pdf"
        job["status"] = "done"
        _stage(job, "done", 100, "完成：%d 页 / %d 块，用时 %.1f 秒" % (model["page_count"], len(units), job["duration"]))
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        job["trace"] = traceback.format_exc()[-1200:]
        _stage(job, "error", job.get("progress", 0), "失败：%s" % e)
    finally:
        _running.discard(job_id)
        _save(job)


def rebuild_only(job_id: str):
    """译文被人工修改后，仅重排版面。"""
    _executor.submit(_rebuild_task, job_id)


def _rebuild_task(job_id: str):
    job = store.load(job_id)
    if not job:
        return
    try:
        job["status"] = "rebuilding"
        _stage(job, "rebuild", 60, "正在重新排版…")
        jdir = store.job_dir(job_id)
        model = json.loads((jdir / "layout.json").read_text(encoding="utf-8"))
        seg = json.loads((jdir / "units.json").read_text(encoding="utf-8"))
        translations = json.loads((jdir / "translations.json").read_text(encoding="utf-8"))
        tids = set(translations)
        protected = {}
        for p in model["pages"]:
            rects = []
            for b in p["blocks"]:
                if b["id"] not in tids:
                    rects.extend([l["bbox"] for l in b["lines"]])
            if rects:
                protected[p["number"]] = rects
        rb = rb_mod.rebuild(str(jdir / "source.pdf"), seg["units"], translations,
                            str(jdir / "out.pdf"), lang=job["opts"].get("tgt_lang", "zh"),
                            protected_rects=protected)
        qa = qa_mod.check(str(jdir / "out.pdf"), str(jdir / "source.pdf"), model, rb,
                          translations, seg["units"])
        for p in ["thumbs"]:
            d = jdir / p
            if d.exists():
                for f in d.glob("*.png"):
                    f.unlink()
        job["qa"] = qa
        job["stats"] = {"rebuild": {k: rb[k] for k in ("units", "shrunk", "overflow", "max_overflow")}}
        job["status"] = "done"
        job["rebuilt_at"] = time.time()
        _stage(job, "done", 100, "已重新排版")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _stage(job, "error", 0, "重新排版失败：%s" % e)
