# -*- coding: utf-8 -*-
"""任务存储：data/jobs/<id>/job.json（原子写 + 重试，避免 Windows 文件锁竞态）。"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from app import config

_WRITE_LOCK = threading.Lock()


def new_id() -> str:
    return time.strftime("%Y%m%d-") + uuid.uuid4().hex[:8]


def job_dir(job_id: str) -> Path:
    d = config.JOBS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(job: dict) -> None:
    d = job_dir(job["id"])
    payload = json.dumps(job, ensure_ascii=False, indent=1)
    tmp = d / ("job.%s.%s.tmp" % (os.getpid(), threading.get_ident()))
    target = d / "job.json"
    with _WRITE_LOCK:
        tmp.write_text(payload, encoding="utf-8")
        for i in range(10):
            try:
                os.replace(tmp, target)
                return
            except (PermissionError, OSError):
                time.sleep(0.03 * (i + 1))
        try:
            target.write_text(payload, encoding="utf-8")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass


def load(job_id: str) -> dict | None:
    p = job_dir(job_id) / "job.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_jobs() -> list:
    out = []
    if not config.JOBS.exists():
        return out
    for d in config.JOBS.iterdir():
        if not d.is_dir():
            continue
        p = d / "job.json"
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            j.pop("log", None)
            j["thumb_ver"] = int((d / "out.pdf").stat().st_mtime) if (d / "out.pdf").exists() else 0
            out.append(j)
        except Exception:
            continue
    out.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return out


def remove(job_id: str) -> bool:
    d = config.JOBS / job_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def sweep_stale(max_age_s: int = 3600) -> int:
    """把服务重启后残留的"排队/进行中"任务标记为中断。"""
    n = 0
    for j in list_jobs():
        if j.get("status") in ("queued", "running", "rebuilding"):
            age = time.time() - j.get("updated_at", 0)
            if age > 180:
                j["status"] = "error"
                j["stage"] = "error"
                j["stage_label"] = "已中断"
                j["error"] = "服务重启导致任务中断，请重新提交"
                save(j)
                n += 1
    return n
