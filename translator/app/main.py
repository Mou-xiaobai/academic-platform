# -*- coding: utf-8 -*-
"""FastAPI 入口：上传、任务、预览、下载、设置。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, runner, store
from app.pipeline import glossary as gl_mod

app = FastAPI(title="版式保真翻译", version="1.0")

try:
    store.sweep_stale()
except Exception:
    pass
app.mount("/static", StaticFiles(directory=str(config.STATIC)), name="static")

MAX_UPLOAD = 90 * 1024 * 1024


@app.get("/")
def index():
    return FileResponse(config.STATIC / "index.html")


@app.get("/api/health")
def health():
    s = config.load_settings()
    return {
        "ok": True,
        "key": bool(config.api_key(s)),
        "model": s.get("model"),
        "version": "1.0",
    }


def _bool(v) -> bool:
    return str(v).lower() in ("1", "true", "on", "yes")


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    tgt_lang: str = Form("zh"),
    src_lang: str = Form("en"),
    skip_references: str = Form("1"),
    model: str = Form(""),
    glossary: str = Form("default"),
):
    name = file.filename or "document.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "目前只支持 PDF 文件")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "文件过大（上限 90MB）")
    if len(data) < 1000:
        raise HTTPException(400, "文件太小，可能不是有效的 PDF")

    jid = store.new_id()
    jdir = store.job_dir(jid)
    (jdir / "source.pdf").write_bytes(data)
    try:
        d = fitz.open(str(jdir / "source.pdf"))
        pages = d.page_count
        d.close()
    except Exception:
        raise HTTPException(400, "无法打开该 PDF（文件可能损坏）")

    s = config.load_settings()
    job = {
        "id": jid,
        "created_at": time.time(),
        "filename": name,
        "size": len(data),
        "pages": pages,
        "status": "queued",
        "stage": "queued",
        "stage_label": "排队中",
        "progress": 0,
        "message": "等待开始…",
        "opts": {
            "tgt_lang": tgt_lang,
            "src_lang": src_lang,
            "skip_references": _bool(skip_references),
            "model": model or s.get("model"),
            "glossary": glossary,
        },
        "log": [],
    }
    store.save(job)
    runner.submit(jid)
    return job


@app.get("/api/jobs")
def jobs():
    return store.list_jobs()


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = store.load(jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    if not store.load(jid):
        raise HTTPException(404, "任务不存在")
    store.remove(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/source")
def source_pdf(jid: str):
    p = store.job_dir(jid) / "source.pdf"
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, media_type="application/pdf", filename="原文件.pdf")


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    p = store.job_dir(jid) / "out.pdf"
    j = store.load(jid)
    if not p.exists():
        raise HTTPException(404, "尚未生成结果")
    base = Path(j["filename"]).stem if j else "translated"
    return FileResponse(p, media_type="application/pdf",
                        filename="%s_中文版.pdf" % base,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{jid}/page/{n}")
def page_png(jid: str, n: int, dpi: int = 96, src: str = "auto"):
    """渲染某一页为 PNG。src=source 强制原文；src=out 强制译文（未生成时报 404）；
    src=auto 优先译文、无译文时回退原文。"""
    jdir = store.job_dir(jid)
    job = store.load(jid)
    if not job:
        raise HTTPException(404, "任务不存在")
    which = src if src in ("source", "out") else "auto"
    out_pdf = jdir / "out.pdf"
    source_pdf = jdir / "source.pdf"
    if which == "source":
        pdf = source_pdf
    elif which == "out":
        pdf = out_pdf
    else:
        pdf = out_pdf if out_pdf.exists() else source_pdf
    cache = jdir / "thumbs"
    cache.mkdir(exist_ok=True)
    f = cache / ("%s_%03d_%d.png" % (which, n, dpi))
    if not f.exists():
        if not pdf.exists():
            raise HTTPException(404, "文件不存在")
        try:
            doc = fitz.open(str(pdf))
            if n < 1 or n > doc.page_count:
                raise HTTPException(404, "页码越界")
            pix = doc[n - 1].get_pixmap(dpi=max(40, min(240, dpi)))
            pix.save(str(f))
            doc.close()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, "渲染失败：%s" % e)
    return FileResponse(f, media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{jid}/blocks")
def blocks(jid: str):
    p = store.job_dir(jid) / "blocks.json"
    if not p.exists():
        raise HTTPException(404, "尚无翻译结果")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


class BlockPatch(BaseModel):
    zh: str


@app.patch("/api/jobs/{jid}/blocks/{bid}")
def patch_block(jid: str, bid: str, payload: BlockPatch):
    jdir = store.job_dir(jid)
    tp = jdir / "translations.json"
    if not tp.exists():
        raise HTTPException(404, "尚无翻译结果")
    tr = json.loads(tp.read_text(encoding="utf-8"))
    if bid not in tr:
        raise HTTPException(404, "块不存在")
    tr[bid] = payload.zh.strip()
    tp.write_text(json.dumps(tr, ensure_ascii=False, indent=0), encoding="utf-8")
    bp = jdir / "blocks.json"
    if bp.exists():
        data = json.loads(bp.read_text(encoding="utf-8"))
        for b in data.get("blocks", []):
            if b["id"] == bid:
                b["zh"] = tr[bid]
        bp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@app.post("/api/jobs/{jid}/rebuild")
def rebuild(jid: str):
    j = store.load(jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    if not (store.job_dir(jid) / "translations.json").exists():
        raise HTTPException(400, "尚无译文可重排")
    j["status"] = "rebuilding"
    store.save(j)
    runner.rebuild_only(jid)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    s = config.load_settings()
    key = config.api_key(s)
    return {
        "model": s.get("model"),
        "concurrency": s.get("concurrency"),
        "batch_chars": s.get("batch_chars"),
        "base_url": s.get("base_url"),
        "has_key": bool(key),
        "key_hint": (key[:6] + "…" + key[-4:]) if len(key) > 12 else "",
        "glossary_size": len(gl_mod.PRESETS.get(s.get("glossary") or "default", {})),
        "data_dir": str(config.DATA),
    }


class SettingsPatch(BaseModel):
    api_key: str | None = None
    model: str | None = None
    concurrency: int | None = None
    batch_chars: int | None = None
    base_url: str | None = None


@app.post("/api/settings")
def post_settings(p: SettingsPatch):
    patch = {k: v for k, v in p.model_dump().items() if v not in (None, "")}
    if patch.get("api_key"):
        patch["api_key"] = patch["api_key"].strip()
    config.save_settings(patch)
    return get_settings()


@app.get("/api/glossary")
def get_glossary():
    return {"presets": {k: len(v) for k, v in gl_mod.PRESETS.items()},
            "default": gl_mod.DEFAULT_GLOSSARY}
