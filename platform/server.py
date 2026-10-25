# -*- coding: utf-8 -*-
"""
无钴富锂锰综述平台 · 本地服务器（纯 Python 标准库，零依赖）
数据即文件：平台内所有编辑直接写入项目 Markdown/JSON 文件。
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))            # .../lrm-review/02_平台
ROOT = os.path.dirname(BASE)                                  # .../lrm-review
APP = os.path.join(BASE, "app")

# 可选配置：02_平台/config.json 覆盖本机路径（文献库根目录 / Zotero.exe）
CFG = {}
try:
    with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
        CFG = json.load(f)
except Exception:
    CFG = {}

D_LIT = os.path.join(ROOT, "04_文献库", "literature.json")
D_OUTLINE = os.path.join(ROOT, "03_大纲", "outline.json")
D_FIGURES = os.path.join(ROOT, "03_大纲", "figures_plan.json")
D_META = os.path.join(BASE, "data", "meta.json")
D_ANALYSIS = os.path.join(ROOT, "01_拆解报告", "原文拆解.md")
D_CH = os.path.join(ROOT, "05_成稿")
D_EXPORT = os.path.join(ROOT, "06_导出")
LIT_ROOT = CFG.get("lit_root") or os.path.join(os.path.expanduser("~"), "Documents", "MyPapers")
D_DLLOG = os.path.join(LIT_ROOT, "下载台账.md")

CHAPTER_FILES = {
    "ch1": ("ch1_引言", "引言", 1200),
    "ch2": ("ch2_材料基础", "材料学基础与去钴逻辑", 2000),
    "ch3": ("ch3_失效机理", "失效机理图谱", 3000),
    "ch4": ("ch4_改性策略", "改性策略体系", 4500),
    "ch5": ("ch5_矩阵与基准", "挑战-策略矩阵与量化基准", 1500),
    "ch6": ("ch6_固态与智能化", "固态电池与智能化路径", 1800),
    "ch7": ("ch7_总结展望", "总结与展望", 800),
}
LEGACY_FOLDERS = ["00_待分类", "01_综述与方法论基底", "02_机理_氧流失与阴离子氧化还原", "03_机理_电压衰减与滞后",
                  "04_机理_界面副反应与Mn溶解", "05_策略_体相掺杂", "06_策略_表面包覆与界面工程",
                  "07_策略_单晶与形貌调控", "08_策略_电解液工程", "09_无钴LRM与固态电池", "10_机器学习与先进表征"]

# ---------------- 多论文项目注册表 ----------------
D_PROJECTS = os.path.join(ROOT, "projects.json")
PROJ_DIR = os.path.join(ROOT, "论文项目")

DEFAULT_CHAPTERS = {
    "ch1": ["ch1_引言", "引言", 1000],
    "ch2": ["ch2_基础与方法", "基础与方法", 2000],
    "ch3": ["ch3_核心内容", "核心内容与分析", 4000],
    "ch4": ["ch4_讨论", "讨论", 2000],
    "ch5": ["ch5_结论", "结论与展望", 800],
}
DEFAULT_FOLDERS = ["00_对标论文", "01_核心文献", "02_方法与基础", "03_结果与讨论", "99_其他"]


def _safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())[:40] or "未命名论文"


def load_projects():
    if os.path.exists(D_PROJECTS):
        try:
            d = load_json(D_PROJECTS, None)
            if d and d.get("projects"):
                return d
        except Exception:
            pass
    d = {"active": "p_lrm", "projects": [
        {"id": "p_lrm", "name": "无钴富锂锰综述", "type": "综述", "created": "2026-10-24", "legacy": True}]}
    save_json(D_PROJECTS, d)
    return d


def save_projects(d):
    save_json(D_PROJECTS, d)


def proj_paths(entry):
    """项目条目 -> 数据路径/配置字典（legacy项目映射到原有固定路径，零迁移）"""
    if entry.get("legacy"):
        return {"lit": D_LIT, "outline": D_OUTLINE, "figures": D_FIGURES, "meta": D_META,
                "analysis": D_ANALYSIS, "ch": D_CH, "export": D_EXPORT, "dllog": D_DLLOG,
                "chapters": entry.get("chapters") or CHAPTER_FILES,
                "folders": entry.get("folders") or LEGACY_FOLDERS, "lit_dir": LIT_ROOT,
                "name": entry["name"], "legacy": True,
                "exp_zh": "综述_中文版", "exp_en": "Review_English"}
    d = os.path.join(PROJ_DIR, entry["id"])
    sn = _safe_name(entry["name"])
    return {"lit": os.path.join(d, "literature.json"),
            "outline": os.path.join(d, "outline.json"),
            "figures": os.path.join(d, "figures_plan.json"),
            "meta": os.path.join(d, "meta.json"),
            "analysis": os.path.join(d, "拆解.md"),
            "ch": os.path.join(d, "成稿"), "export": os.path.join(d, "导出"),
            "dllog": "", "chapters": entry.get("chapters") or json.loads(json.dumps(DEFAULT_CHAPTERS)),
            "folders": entry.get("folders") or list(DEFAULT_FOLDERS),
            "lit_dir": entry.get("lit_dir") or os.path.join(LIT_ROOT, sn),
            "name": entry["name"], "legacy": False,
            "exp_zh": sn + "_中文版", "exp_en": sn + "_English"}


def cur_paths():
    d = load_projects()
    for e in d["projects"]:
        if e["id"] == d.get("active"):
            return proj_paths(e)
    return proj_paths(d["projects"][0])


def lit_key(rec):
    doi = (rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    pp = rec.get("pdf_path")
    if pp:
        return "path:" + os.path.normpath(pp).lower()
    t = _norm_title(rec.get("title") or "")
    return ("title:" + t) if t else ""


def mark_shared():
    """跨项目共享标签：同一文献（DOI/路径/标题）被多个论文项目收录时，互写 shared_with"""
    prj = load_projects()["projects"]
    if len(prj) < 2:
        return
    libs = {}
    for e in prj:
        pp = proj_paths(e)
        if os.path.exists(pp["lit"]):
            libs[e["id"]] = (e["name"], pp["lit"], load_json(pp["lit"], []))
    byk = {}
    for pid, (name, path, lit) in libs.items():
        for i, r in enumerate(lit):
            k = lit_key(r)
            if k and not k.startswith("title:"):
                byk.setdefault(k, []).append((pid, i))
    dirty = set()
    for k, refs in byk.items():
        if len(set(pid for pid, _ in refs)) < 2:
            continue
        names = {libs[pid][0] for pid, _ in refs}
        for pid, i in refs:
            r = libs[pid][2][i]
            want = sorted(names - {libs[pid][0]})
            if r.get("shared_with") != want:
                r["shared_with"] = want
                dirty.add(pid)
    for pid in dirty:
        _, path, lit = libs[pid]
        save_json(path, lit)


# ---------------- DeepSeek：对标论文AI拆解 ----------------
def ds_api_key():
    try:
        p = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
        m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", open(p, encoding="utf-8").read())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY", "")


def ds_chat(messages, json_mode=False, max_tokens=5000, timeout=280):
    key = ds_api_key()
    if not key:
        raise RuntimeError("未找到 DeepSeek API key（~/.dsh/.credentials.yaml）")
    body = {"model": "deepseek-chat", "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"]


def pdf_text_extract(path, cap=90000):
    import fitz
    doc = fitz.open(path)
    parts, total = [], 0
    for i, pg in enumerate(doc):
        t = pg.get_text()
        parts.append(f"\n[[第{i + 1}页]]\n{t}")
        total += len(t)
        if total > cap:
            parts.append("\n…（篇幅所限，后续内容已截断）")
            break
    doc.close()
    return "".join(parts)


def _ds_parse_json(txt):
    txt = re.sub(r"^```(json)?|```$", "", (txt or "").strip(), flags=re.M).strip()
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m:
        raise ValueError("AI未返回JSON对象")
    return json.loads(m.group(0))


def decompose_benchmark(pdf_path, proj_name, ptype):
    """对标论文 -> 拆解报告md + 大纲/章节/分类文件夹 JSON。返回 dict 或抛异常"""
    text = pdf_text_extract(pdf_path)
    if len(text) < 800:
        raise ValueError("PDF文本过少（可能是扫描件），无法AI拆解")
    sys1 = ("你是一位顶刊论文写作分析专家。用户将撰写一篇对标此论文的" + ptype +
            "。请输出中文Markdown拆解报告，结构严格为：\n"
            "# 拆解报告\n## 一、基本信息（从文本推断：标题/期刊/年份/主题）\n"
            "## 二、章节骨架（逐章：作用+篇幅占比+核心论点）\n"
            "## 三、叙事逻辑（引线是什么、如何过渡、详略如何取舍）\n"
            "## 四、写作模板（可复用的段落组织方式，如\"机制→策略→效果\"）\n"
            "## 五、图表策略（图的类型/密度/信息组织方式）\n"
            "## 六、可借鉴句式（5-8条，英文原句+中文译法）\n"
            "## 七、差异化建议（用户写自己的论文时如何避免同质化）\n只输出报告本身。")
    analysis = ds_chat([{"role": "system", "content": sys1},
                        {"role": "user", "content": "论文全文如下：\n" + text}], max_tokens=4500)
    sys2 = ("你是学术论文结构设计师。基于对标论文的写法，为用户的论文《" + proj_name + "》（类型：" + ptype +
            "）设计结构。只输出一个json对象，不要多余文字，格式：\n"
            '{"chapters":[{"id":"ch1","stem":"ch1_短名(文件名用,字母数字下划线)","title":"中文章标题",'
            '"target":章字数目标整数},...共5-8章],\n'
            '"outline":[{"chapter":"ch1","title":"小节标题","words":字数,"refs":文献配额整数,'
            '"points":"该小节要点(1-2句)"},...每章2-4个小节],\n'
            '"folders":["00_对标论文","01_...","02_..."](5-8个分类文件夹，NN_主题格式，贴合该研究方向)}\n'
            "章节设计要参考对标论文的骨架但适配用户自己的题目。")
    j = _ds_parse_json(ds_chat([{"role": "system", "content": sys2},
                                {"role": "user", "content": "对标论文全文：\n" + text[:60000]}],
                               json_mode=True, max_tokens=4500))
    chapters = {}
    for i, c in enumerate(j.get("chapters") or []):
        cid = re.sub(r"\W+", "", c.get("id") or f"ch{i + 1}")[:12] or f"ch{i + 1}"
        chapters[cid] = [re.sub(r'[\\/:*?"<>| ]+', "_", c.get("stem") or cid)[:30],
                         (c.get("title") or cid)[:40], int(c.get("target") or 1500)]
    if len(chapters) < 3:
        raise ValueError("AI返回的章节数不足")
    folders = [f for f in (j.get("folders") or []) if isinstance(f, str) and f.strip()]
    outline = []
    for o in j.get("outline") or []:
        if o.get("chapter") in chapters and o.get("title"):
            outline.append({"chapter": o["chapter"], "title": o["title"][:60],
                            "words": int(o.get("words") or 500), "refs": int(o.get("refs") or 5),
                            "status": "未开始", "points": (o.get("points") or "")[:200]})
    return {"analysis": analysis, "chapters": chapters, "outline": outline, "folders": folders}

# ---------------- Zotero 本地集成 ----------------
ZOT = "http://127.0.0.1:23119"
ZOTERO_EXE = CFG.get("zotero_exe") or r"D:\zotero\zotero.exe"


def zot_ping():
    try:
        req = urllib.request.Request(ZOT + "/connector/ping")
        with urllib.request.urlopen(req, timeout=3) as r:
            return True, r.headers.get("X-Zotero-Version", "unknown")
    except Exception:
        return False, ""


def zot_ensure_running(timeout=40):
    ok, ver = zot_ping()
    if ok:
        return True, ver
    try:
        if os.path.exists(ZOTERO_EXE):
            subprocess.Popen([ZOTERO_EXE], cwd=os.path.dirname(ZOTERO_EXE))
        else:
            os.startfile("zotero://open-library")
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        ok, ver = zot_ping()
        if ok:
            return True, ver
    return False, ""


def _norm_title(t):
    """标题归一化：统一连字符/空白/大小写"""
    t = re.sub(r"[‐‑‒–—―−]", "-", t or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def zot_db_find(doi=None, title=None):
    """拷贝 zotero.sqlite(+wal) 只读查询：按 DOI/标题 反查最新 item key（兼容 Zotero 运行中的锁）"""
    db = os.path.expandvars(r"%USERPROFILE%\Zotero\zotero.sqlite")
    for p in __import__("glob").glob(os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles\*\prefs.js")):
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
            for ln in txt.splitlines():
                if "extensions.zotero.dataDir" in ln and '", "' in ln:
                    v = ln.split('", "')[1].rsplit('")')[0].replace("\\\\", "\\")
                    if os.path.exists(os.path.join(v, "zotero.sqlite")):
                        db = os.path.join(v, "zotero.sqlite")
        except Exception:
            pass
    if not os.path.exists(db):
        return None
    tmp = os.path.join(tempfile.gettempdir(), "zot_key.sqlite")
    try:
        shutil.copy2(db, tmp)
        for ext in ("-wal", "-shm"):
            try:
                shutil.copy2(db + ext, tmp + ext)
            except Exception:
                pass
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        row = None
        if doi:
            rows = cur.execute(
                """SELECT i.key, v.value FROM itemData d
                   JOIN itemDataValues v ON d.valueID = v.valueID
                   JOIN items i ON d.itemID = i.itemID
                   JOIN fields f ON d.fieldID = f.fieldID
                   JOIN itemTypes t ON i.itemTypeID = t.itemTypeID
                   WHERE f.fieldName='DOI' AND v.value LIKE ?
                   ORDER BY i.itemID DESC LIMIT 10""", (f"%{doi}%",)).fetchall()
            dl = doi.lower().strip()
            for k, v in rows:  # 精确匹配优先
                vv = v.strip().lower().rstrip("/")
                if vv == dl or vv.endswith("/" + dl) or vv.endswith(dl):
                    row = (k, v)
                    break
            if row is None and rows:
                row = rows[0]
        if row is None and title:
            nt = _norm_title(title)
            prefix = nt[:40].replace("%", r"\%").replace("_", r"\_")
            rows = cur.execute(
                """SELECT i.key, v.value FROM itemData d
                   JOIN itemDataValues v ON d.valueID = v.valueID
                   JOIN items i ON d.itemID = i.itemID
                   JOIN fields f ON d.fieldID = f.fieldID
                   WHERE f.fieldName='title' AND v.value LIKE ? ESCAPE '\'
                   ORDER BY i.itemID DESC LIMIT 10""", (prefix + "%",)).fetchall()
            for k, v in rows:
                if _norm_title(v) == nt:
                    row = (k, v)
                    break
            if row is None and rows:
                row = rows[0]
        con.close()
        try:
            os.remove(tmp)
        except Exception:
            pass
        return row[0] if row else None
    except Exception:
        return None


def zot_att_find(parent_key):
    """拷贝 zotero.sqlite(+wal) 查询父条目下的PDF附件: 返回 (附件key, libraryID) 或 None"""
    db = os.path.expandvars(r"%USERPROFILE%\Zotero\zotero.sqlite")
    for p in __import__("glob").glob(os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles\*\prefs.js")):
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
            for ln in txt.splitlines():
                if "extensions.zotero.dataDir" in ln and '", "' in ln:
                    v = ln.split('", "')[1].rsplit('")')[0].replace("\\\\", "\\")
                    if os.path.exists(os.path.join(v, "zotero.sqlite")):
                        db = os.path.join(v, "zotero.sqlite")
        except Exception:
            pass
    if not os.path.exists(db):
        return None
    tmp = os.path.join(tempfile.gettempdir(), "zot_att.sqlite")
    try:
        shutil.copy2(db, tmp)
        for ext in ("-wal", "-shm"):
            try:
                shutil.copy2(db + ext, tmp + ext)
            except Exception:
                pass
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        row = cur.execute(
            """SELECT i.key, i.libraryID FROM itemAttachments a
               JOIN items i ON a.itemID = i.itemID
               JOIN items p ON p.itemID = a.parentItemID
               LEFT JOIN deletedItems d ON d.itemID = p.itemID
               WHERE p.key = ? AND a.contentType='application/pdf' AND d.itemID IS NULL
               ORDER BY i.itemID DESC LIMIT 1""", (parent_key,)).fetchone()
        con.close()
        try:
            os.remove(tmp)
        except Exception:
            pass
        return row if row else None
    except Exception:
        return None


def zot_save_item(rec):
    """通过 connector/saveItems 写入条目（含PDF附件，附件走本平台静态服务）"""
    doi = rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or ""
    atts = []
    if rec.get("pdf_path") and os.path.exists(rec["pdf_path"]):
        rel = os.path.relpath(rec["pdf_path"], LIT_ROOT).replace("\\", "/")
        atts.append({"url": f"http://127.0.0.1:8320/pdf/{urllib.parse.quote(rel)}",
                     "title": "PDF 全文", "mimeType": "application/pdf"})
    creators = []
    for a in (rec.get("authors") or "").split(","):
        a = a.strip()
        if not a or a.lower() in ("et al.", "et al"):
            continue
        parts = a.split()
        if len(parts) >= 2:
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})
        else:
            creators.append({"creatorType": "author", "lastName": a, "firstName": ""})
        if len(creators) >= 6:
            break
    item = {
        "itemType": "journalArticle",
        "title": rec.get("title") or "Untitled",
        "creators": creators,
        "DOI": doi,
        "date": str(rec.get("year") or ""),
        "publicationTitle": rec.get("journal") or "",
        "url": f"https://doi.org/{doi}" if doi else "",
        "tags": [{"tag": rec.get("folder") or "未分类"}],
        "attachments": atts,
    }
    payload = json.dumps({"items": [item], "uri": "http://127.0.0.1:8320/"}).encode()
    req = urllib.request.Request(ZOT + "/connector/saveItems", data=payload, headers={
        "Content-Type": "application/json",
        "X-Zotero-Connector-API-Version": "3",
        "X-Zotero-Version": "5.0.97",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        r.read()
    return True


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def ch_path(cid, lang="zh", P=None):
    P = P or cur_paths()
    stem = P["chapters"][cid][0]
    suffix = "" if lang == "zh" else "_en"
    return os.path.join(P["ch"], f"{stem}{suffix}.md")


def count_words(text):
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]+", text))
    return cjk, latin


# ---------------- PDF 工具 ----------------
def pdf_optimize_one(path):
    """压缩优化单个PDF（垃圾回收+全对象deflate），返回 (before, after) 或 None(失败)"""
    try:
        import fitz
        doc = fitz.open(path)
        if doc.needs_pass:
            doc.close()
            return None
        tmp = path + ".opt_tmp"
        doc.save(tmp, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
        doc.close()
        before, after = os.path.getsize(path), os.path.getsize(tmp)
        with open(tmp, "rb") as f:
            head = f.read(5)
        if head == b"%PDF-" and after > 1000:
            os.replace(tmp, path)
            return (before, after)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return None
    except Exception:
        try:
            os.remove(path + ".opt_tmp")
        except Exception:
            pass
        return None


def pdf_pagespec(spec, total):
    """解析 '1-3,5' 页码描述 -> [(a,b)...] 1-based"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
        else:
            a = b = int(part)
        a = max(1, a)
        b = min(total, b)
        if a <= b:
            out.append((a, b))
    return out


# ---------------- 翻译服务对接（digital/translator, 端口8321） ----------------
TR = "http://127.0.0.1:8321"


def tr_ping():
    try:
        with urllib.request.urlopen(TR + "/api/health", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def tr_ensure():
    """确保翻译服务运行，未运行则拉起（最长等25秒）。优先仓库内置 translator/，其次本机既有安装"""
    if tr_ping():
        return True
    cands = []
    td = os.path.join(ROOT, "translator")
    if os.path.isdir(td):
        cands.append(("uvicorn", td))
    legacy = os.path.join(ROOT, "_tools", "run_translator.py")
    if os.path.exists(legacy):
        cands.append(("script", legacy))
    for kind, target in cands:
        try:
            if kind == "uvicorn":
                subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                                  "--host", "127.0.0.1", "--port", "8321", "--log-level", "warning"],
                                 cwd=target, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen([sys.executable, target], cwd=os.path.dirname(target),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _ in range(25):
                time.sleep(1)
                if tr_ping():
                    return True
        except Exception:
            continue
    return False


def tr_call(method, path, body=None, timeout=180):
    """转发 JSON 请求到翻译服务"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(TR + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"raw_len": len(raw)}


def tr_create_job(pdf_path, skip_refs=True):
    """multipart 上传本地PDF到翻译服务创建任务"""
    import uuid
    fn = os.path.basename(pdf_path)
    with open(pdf_path, "rb") as f:
        content = f.read()
    bnd = uuid.uuid4().hex
    chunks = []
    for name, val in [("tgt_lang", "zh"), ("src_lang", "en"),
                      ("skip_references", "1" if skip_refs else "0"),
                      ("glossary", "default")]:
        chunks.append(
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8"))
    chunks.append(
        (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fn}\"\r\n"
         f"Content-Type: application/pdf\r\n\r\n").encode("utf-8") + content + b"\r\n")
    chunks.append(f"--{bnd}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    req = urllib.request.Request(TR + "/api/jobs", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ---------------- 批量导入：元数据提取与自动分类 ----------------
AUTO_RULES = [
    ("02_机理_氧流失与阴离子氧化还原", ["oxygen redox", "anionic redox", "oxygen loss", "oxygen release", "lattice oxygen", "trapped o2", "氧氧化还原", "析氧"]),
    ("03_机理_电压衰减与滞后", ["voltage decay", "voltage fade", "voltage hysteresis", "hysteresis", "电压衰减", "电压滞后"]),
    ("04_机理_界面副反应与Mn溶解", ["mn dissolution", "cei", "cathode electrolyte interphase", "side reaction", "界面副反应", "溶解"]),
    ("05_策略_体相掺杂", ["doping", "doped", "dopant", "high-entropy", "掺杂"]),
    ("06_策略_表面包覆与界面工程", ["coating", "surface modification", "surface engineering", "包覆", "表面改性", "表面修饰"]),
    ("07_策略_单晶与形貌调控", ["single-crystal", "single crystal", "core-shell", "concentration gradient", "morphology", "单晶", "核壳", "浓度梯度"]),
    ("08_策略_电解液工程", ["electrolyte", "电解液", "电解质液"]),
    ("09_无钴LRM与固态电池", ["solid-state", "solid state", "assb", "all-solid", "sulfide electrolyte", "halide electrolyte", "固态"]),
    ("10_机器学习与先进表征", ["machine learning", "neural network", "data-driven", "nmr", "rixs", "neutron", "xas", "characterization", "机器学习", "表征"]),
    ("01_综述与方法论基底", ["review", "progress, challenges", "perspective", "outlook", "综述", "进展", "挑战与展望"]),
]
LRM_RULES = ["li-rich", "lithium-rich", "lrm", "lrmo", "富锂"]


def auto_classify(title):
    t = (title or "").lower()
    if not any(k in t for k in LRM_RULES):
        best = None
        for folder, kws in AUTO_RULES:
            if any(k in t for k in kws):
                best = folder
                break
        return best or "00_待分类"
    for folder, kws in AUTO_RULES:
        hits = sum(1 for k in kws if k in t)
        if hits:
            return folder
    return "00_待分类"


def pdf_meta_extract(path):
    """从PDF提取 DOI/标题/作者/年份（首页文本启发式 + Crossref 增强）"""
    import fitz
    out = {"pdf_path": os.path.abspath(path), "title": "", "authors": "",
           "journal": "", "year": None, "doi": "", "source": "PDF导入"}
    try:
        doc = fitz.open(path)
        meta = doc.metadata or {}
        text1 = doc[0].get_text("text")[:3500]
        text2 = doc[1].get_text("text")[:2000] if doc.page_count > 1 else ""
        doc.close()
    except Exception:
        return out
    m = re.search(r"(?:doi\.org/|doi:\s*|DOI:\s*)?(10\.\d{4,9}/[^\s\"<>\]},;]+)", text1 + " " + text2)
    if m:
        out["doi"] = m.group(1).rstrip(".")
    t = (meta.get("title") or "").strip()
    if t and len(t) > 8 and "untitled" not in t.lower():
        out["title"] = t
    else:
        # 首页前几行里挑最长的英文行作为标题候选
        lines = [ln.strip() for ln in text1.split("\n") if 25 < len(ln.strip()) < 200]
        out["title"] = max(lines[:8], key=len) if lines else os.path.basename(path)
    a = (meta.get("author") or "").strip()
    if a:
        out["authors"] = a
    else:
        ma = re.search(r"^([A-Z][\w\-.]+(?:\s+[A-Z]\.?)+)(?:,\s*[A-Z][\w\-.]+\s+[A-Z]\.?){1,8}", text1, re.M)
        out["authors"] = (ma.group(0)[:80] + ", et al.") if ma else ""
    my = re.search(r"\b(19|20)\d{2}\b", text1[:1200])
    if my:
        y = int(my.group(0))
        if 1990 <= y <= 2030:
            out["year"] = y
    # Crossref 增强
    if out["doi"]:
        for opener in (urllib.request.build_opener(urllib.request.ProxyHandler({})), urllib.request.build_opener()):
            try:
                req = urllib.request.Request(f"https://api.crossref.org/works/{urllib.parse.quote(out['doi'])}",
                                             headers={"User-Agent": "academic-platform/1.0"})
                with opener.open(req, timeout=12) as r:
                    w = json.loads(r.read())["message"]
                out["title"] = (w.get("title") or [out["title"]])[0]
                aus = w.get("author") or []
                if aus:
                    out["authors"] = (aus[0].get("family", "") + (", " if len(aus) > 1 else "") +
                                      ("et al." if len(aus) > 1 else ""))
                out["journal"] = (w.get("container-title") or [""])[0]
                dp = (w.get("issued", {}).get("date-parts") or [[None]])[0]
                out["year"] = dp[0] or out["year"]
                break
            except Exception:
                continue
    return out


def parse_ris(text):
    """解析RIS/BibTeX文本 -> 题录列表"""
    items = []
    text = text.strip()
    if text.startswith("@"):
        for m in re.finditer(r"@(\w+)\{[^,]*,(.*?)\n\}", text, re.S):
            body = m.group(2)
            it = {"source": "BibTeX导入"}
            for fm, key in [(r'title\s*=\s*[{"](.+?)[}"]', "title"), (r'author\s*=\s*[{"](.+?)[}"]', "authors"),
                            (r'journal\s*=\s*[{"](.+?)[}"]', "journal"), (r'doi\s*=\s*[{"](.+?)[}"]', "doi"),
                            (r'year\s*=\s*[{"](\d{4})[}"]', "year")]:
                mm = re.search(fm, body, re.I | re.S)
                if mm:
                    it[key] = mm.group(1).replace("\n", " ").strip()
                else:
                    it.setdefault(key, "" if key != "year" else None)
            if it.get("title"):
                items.append(it)
        return items
    cur = {}
    last_tag = None
    for ln in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", ln.strip())
        if not m:
            if last_tag and cur.get(last_tag) is not None:
                cur[last_tag] += " " + ln.strip()
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"source": "RIS导入"}
            continue
        if tag in ("ER",):
            if cur.get("title") or cur.get("TI") or cur.get("T1"):
                items.append(cur)
            cur = {}
            last_tag = None
            continue
        key = {"TI": "title", "T1": "title", "AU": "authors", "A1": "authors", "DO": "doi",
               "JO": "journal", "T2": "journal", "PY": "year", "Y1": "year"}.get(tag)
        if key:
            if key in cur and cur[key]:
                cur[key] += "; " + val if key == "authors" else " " + val
            else:
                cur[key] = val
            last_tag = key
    if cur.get("title"):
        items.append(cur)
    out = []
    for it in items:
        aus = it.get("authors", "")
        if ";" in aus:
            first = aus.split(";")[0].strip()
            aus = first + (", et al." if aus.count(";") > 1 else "")
        out.append({"title": it.get("title", ""), "authors": aus, "journal": it.get("journal", ""),
                    "year": int(it["year"][:4]) if (it.get("year") or "").isdigit() else None,
                    "doi": it.get("doi", ""), "source": it.get("source", "RIS导入")})
    return out


def next_import_id(lit):
    mx = 0
    for r in lit:
        n = r.get("orig_num") or 0
        if isinstance(n, int) and n > mx:
            mx = n
    return mx + 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path, mime):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or "json" in mime or "javascript" in mime else ""))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/" or p == "/index.html":
            return self._file(os.path.join(APP, "index.html"), "text/html")
        if p.startswith("/static/"):
            name = os.path.basename(p)
            mime = {"js": "text/javascript", "css": "text/css", "png": "image/png", "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            return self._file(os.path.join(APP, name), mime)
        if p.startswith("/pdf/"):
            # 本地PDF静态服务（供Zotero抓取附件），防目录穿越
            rel = urllib.parse.unquote(p[len("/pdf/"):])
            base = os.path.normpath(LIT_ROOT)
            full = os.path.normpath(os.path.join(base, rel))
            if not full.startswith(base) or not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            return self._file(full, "application/pdf")
        if p == "/api/data":
            P = cur_paths()
            prj = load_projects()
            act_id = prj.get("active") or prj["projects"][0]["id"]
            outline = load_json(P["outline"], [])
            figures = load_json(P["figures"], [])
            meta = load_json(P["meta"], {"todos": [], "log": []})
            lit = load_json(P["lit"], [])
            chapters = []
            for cid, cval in P["chapters"].items():
                title = cval[1]
                for lang in ("zh", "en"):
                    fp = ch_path(cid, lang, P)
                    text = ""
                    if os.path.exists(fp):
                        with open(fp, encoding="utf-8") as f:
                            text = f.read()
                    cjk, latin = count_words(text)
                    chapters.append({"id": cid, "lang": lang, "title": title,
                                     "words": cjk if lang == "zh" else latin, "text_len": len(text)})
            analysis = ""
            if P["analysis"] and os.path.exists(P["analysis"]):
                with open(P["analysis"], encoding="utf-8") as f:
                    analysis = f.read()
            # 文献中文版状态：已是中文版 -> zh_self；找到对应 _中文版.pdf -> zh_pdf
            zh_files = [r2.get("pdf_path") for r2 in lit
                        if r2.get("pdf_path") and "_中文版" in os.path.basename(r2["pdf_path"])]
            for r in lit:
                pp = r.get("pdf_path")
                if not pp:
                    continue
                b = os.path.basename(pp)
                if "_中文版" in b:
                    r["zh_self"] = True
                    continue
                stem = os.path.splitext(b)[0]
                hit = next((z for z in zh_files if os.path.basename(z).startswith(stem + "_中文版")), None)
                if hit:
                    r["zh_pdf"] = hit
                    continue
                cand = os.path.join(os.path.dirname(pp), stem + "_中文版.pdf")
                if os.path.exists(cand):
                    r["zh_pdf"] = cand
            dllog = ""
            if P["dllog"] and os.path.exists(P["dllog"]):
                try:
                    with open(P["dllog"], encoding="utf-8") as f:
                        dllog = f.read()
                except Exception:
                    dllog = ""
            act_entry = next((e for e in prj["projects"] if e["id"] == act_id), prj["projects"][0])
            chapters_meta = [{"id": cid, "stem": v[0], "title": v[1],
                              "target": v[2] if len(v) > 2 else 0} for cid, v in P["chapters"].items()]
            projects_meta = [{"id": e["id"], "name": e["name"], "type": e.get("type", ""),
                              "legacy": bool(e.get("legacy")), "created": e.get("created", "")}
                             for e in prj["projects"]]
            return self._json({"outline": outline, "literature": lit, "figures": figures,
                               "meta": meta, "chapters": chapters, "analysis_md": analysis,
                               "dl_log_md": dllog, "lit_root": P["lit_dir"],
                               "pid": act_id, "pname": P["name"], "ptype": act_entry.get("type", ""),
                               "legacy": P["legacy"], "projects": projects_meta,
                               "chapters_meta": chapters_meta, "folders": P["folders"],
                               "benchmark": act_entry.get("benchmark") or {},
                               "analysis_path": P["analysis"]})
        if p == "/api/chapter":
            q = parse_qs(u.query)
            cid, lang = q.get("id", ["ch1"])[0], q.get("lang", ["zh"])[0]
            fp = ch_path(cid, lang)
            text = ""
            if os.path.exists(fp):
                with open(fp, encoding="utf-8") as f:
                    text = f.read()
            return self._json({"id": cid, "lang": lang, "text": text})

        if p == "/api/tr/page":
            # 翻译页预览PNG（<img src>走GET）: ?jid=&n=&src=source|out|auto&dpi=40..240
            q = parse_qs(u.query)
            jid = q.get("jid", [""])[0]
            try:
                n = str(max(1, int(q.get("n", ["1"])[0])))
            except Exception:
                n = "1"
            src = q.get("src", ["auto"])[0]
            if src not in ("source", "out"):
                src = "auto"
            try:
                dpi = max(40, min(240, int(q.get("dpi", ["96"])[0])))
            except Exception:
                dpi = 96
            try:
                req2 = urllib.request.Request(f"{TR}/api/jobs/{jid}/page/{n}?src={src}&dpi={dpi}")
                with urllib.request.urlopen(req2, timeout=60) as r2:
                    png = r2.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
            except Exception as e:
                return self._json({"error": str(e)[:80]}, 500)
            return

        if p == "/api/tr/file":
            # 译文/原文PDF下载（<a href>走GET）: ?jid=&kind=out|source
            q = parse_qs(u.query)
            jid = q.get("jid", [""])[0]
            kind = q.get("kind", ["out"])[0]
            path2 = "download" if kind == "out" else "source"
            try:
                req2 = urllib.request.Request(f"{TR}/api/jobs/{jid}/{path2}")
                with urllib.request.urlopen(req2, timeout=120) as r2:
                    pdf = r2.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="translated_{kind}.pdf"')
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)
            except Exception as e:
                return self._json({"error": str(e)[:80]}, 500)
            return

        if p == "/api/pdf_info":
            """任意本地PDF信息: ?path= -> {pages}"""
            q = parse_qs(u.query)
            path2 = urllib.parse.unquote(q.get("path", [""])[0])
            if not (path2.lower().endswith(".pdf") and os.path.exists(path2)):
                return self._json({"error": "pdf not found"}, 404)
            try:
                import fitz
                doc = fitz.open(path2)
                n = doc.page_count
                doc.close()
                return self._json({"ok": True, "pages": n})
            except Exception as e:
                return self._json({"error": str(e)[:80]}, 500)

        if p == "/api/pdf_page":
            """任意本地PDF页渲染PNG（文献库阅读器用）: ?path=&n=&dpi="""
            q = parse_qs(u.query)
            path2 = urllib.parse.unquote(q.get("path", [""])[0])
            try:
                n = max(1, int(q.get("n", ["1"])[0]))
            except Exception:
                n = 1
            try:
                dpi = max(40, min(240, int(q.get("dpi", ["96"])[0])))
            except Exception:
                dpi = 96
            if not (path2.lower().endswith(".pdf") and os.path.exists(path2)):
                return self._json({"error": "pdf not found"}, 404)
            try:
                import fitz
                doc = fitz.open(path2)
                if n > doc.page_count:
                    raise ValueError("page out of range")
                pix = doc[n - 1].get_pixmap(dpi=dpi)
                png = pix.tobytes("png")
                doc.close()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
            except Exception as e:
                return self._json({"error": str(e)[:80]}, 500)
            return

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        try:
            data = self._body()
        except Exception:
            return self._json({"error": "bad json"}, 400)
        P = cur_paths()

        if p == "/api/chapter":
            cid, lang = data.get("id"), data.get("lang", "zh")
            if cid not in P["chapters"]:
                return self._json({"error": "bad chapter"}, 400)
            os.makedirs(P["ch"], exist_ok=True)
            with open(ch_path(cid, lang, P), "w", encoding="utf-8") as f:
                f.write(data.get("text", ""))
            return self._json({"ok": True})

        if p == "/api/outline":
            save_json(P["outline"], data.get("outline", []))
            return self._json({"ok": True})
        if p == "/api/figures":
            save_json(P["figures"], data.get("figures", []))
            return self._json({"ok": True})
        if p == "/api/meta":
            save_json(P["meta"], data)
            return self._json({"ok": True})
        if p == "/api/literature":
            save_json(P["lit"], data.get("literature", []))
            mark_shared()
            return self._json({"ok": True})
        if p == "/api/open":
            path = (data.get("path") or "").strip()
            if not path or not os.path.exists(path):
                return self._json({"ok": False, "error": "文件不存在（可能被移动/重命名）", "path": path}, 404)
            try:
                if os.path.isdir(path):
                    os.startfile(path)
                else:
                    os.startfile(path)  # 用系统默认程序打开（PDF -> WPS/浏览器）
                return self._json({"ok": True, "path": path})
            except Exception as e:
                return self._json({"ok": False, "error": f"系统打开失败: {e}"}, 500)

        if p == "/api/upload_pdf":
            import base64
            path = data.get("path", "")
            b64 = data.get("b64", "")
            folder = os.path.dirname(path)
            if not os.path.normpath(folder).startswith(os.path.normpath(os.path.dirname(LIT_ROOT))):
                return self._json({"error": "bad path"}, 400)
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return self._json({"error": "bad b64"}, 400)
            if not raw.startswith(b"%PDF-") or len(raw) < 15000:
                return self._json({"error": "not a pdf", "head": raw[:8].hex()}, 400)
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
            # 可选：回链文献库
            num = data.get("num")
            linked = False
            if num is not None:
                try:
                    lit = load_json(P["lit"], [])
                    for r in lit:
                        if r.get("orig_num") == num:
                            r["pdf_path"] = path
                            linked = True
                            break
                    if linked:
                        save_json(P["lit"], lit)
                except Exception:
                    linked = False
            return self._json({"ok": True, "size": len(raw), "path": path, "linked": linked})

        if p == "/api/dlqueue":
            """返回浏览器下载队列的下一篇（跳过已有pdf_path的）"""
            qpath = os.path.join(ROOT, "_tools", "browser_queue.json")
            try:
                with open(qpath, encoding="utf-8") as f:
                    queue = json.load(f)
            except Exception:
                return self._json({"error": "no queue"}, 404)
            with open(P["lit"], encoding="utf-8") as f:
                lit = json.load(f)
            have = {r["orig_num"] for r in lit if r.get("pdf_path")}
            spath = os.path.join(ROOT, "_tools", "dl_state2.json")
            st = {}
            try:
                with open(spath, encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                pass
            SKIP = ("no-access", "manual", "captcha", "doi-mismatch", "upload-fail", "no-entitle")
            want_group = data.get("group")  # 可选：只返回该组的下一篇
            item = None
            for x in queue:
                if x["num"] in have:
                    continue
                if want_group and x.get("group") != want_group:
                    continue
                if st.get(str(x["num"])) in SKIP:
                    continue
                item = x
                break
            remaining = sum(1 for x in queue if x["num"] not in have
                            and (not want_group or x.get("group") == want_group)
                            and st.get(str(x["num"])) not in SKIP)
            return self._json({"next": item, "remaining": remaining, "total": len(queue)})

        if p == "/api/dlmark":
            """浏览器下载状态回写: {num, status, detail} -> dl_state2.json"""
            spath = os.path.join(ROOT, "_tools", "dl_state2.json")
            try:
                with open(spath, encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                st = {}
            st[str(data.get("num"))] = data.get("status", "?")
            with open(spath, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
            return self._json({"ok": True})

        if p == "/api/zotero/scan":
            """反向回录：扫描Zotero数据库与storage，把匹配的PDF拷入平台分类文件夹并记key"""
            import glob as _glob
            data_dir = os.path.expandvars(r"%USERPROFILE%\Zotero")
            for p in _glob.glob(os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles\*\prefs.js")):
                try:
                    txt = open(p, encoding="utf-8", errors="ignore").read()
                    for ln in txt.splitlines():
                        if "extensions.zotero.dataDir" in ln and '", "' in ln:
                            v = ln.split('", "')[1].rsplit('")')[0].replace("\\\\", "\\")
                            if os.path.exists(os.path.join(v, "zotero.sqlite")):
                                data_dir = v
                except Exception:
                    pass
            db = os.path.join(data_dir, "zotero.sqlite")
            if not os.path.exists(db):
                return self._json({"ok": False, "error": "未找到 zotero.sqlite"}, 404)
            tmp = os.path.join(tempfile.gettempdir(), "zot_scan.sqlite")
            try:
                shutil.copy2(db, tmp)
                for ext in ("-wal", "-shm"):
                    try:
                        shutil.copy2(db + ext, tmp + ext)
                    except Exception:
                        pass
            except Exception as e:
                return self._json({"ok": False, "error": f"数据库拷贝失败: {str(e)[:80]}"}, 500)

            try:
                con = sqlite3.connect(tmp)
                cur = con.cursor()
                # 所有普通条目: key, DOI, title, itemType
                items = cur.execute(
                    """SELECT i.itemID, i.key, t.typeName,
                          (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
                            JOIN fields f ON d.fieldID=f.fieldID WHERE d.itemID=i.itemID AND f.fieldName='DOI' LIMIT 1),
                          (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
                            JOIN fields f ON d.fieldID=f.fieldID WHERE d.itemID=i.itemID AND f.fieldName='title' LIMIT 1)
                       FROM items i JOIN itemTypes t ON i.itemTypeID=t.itemTypeID
                       WHERE t.typeName IN ('journalArticle','report','thesis','conferencePaper','book','bookSection')""").fetchall()
                # 附件映射: parent -> (attachmentItemID, linkMode, path)
                atts = cur.execute(
                    """SELECT a.parentItemID, a.itemID, a.linkMode, a.path, a.contentType FROM itemAttachments a
                       WHERE a.path IS NOT NULL""").fetchall()
                con.close()
            except Exception as e:
                return self._json({"ok": False, "error": f"数据库读取失败: {str(e)[:80]}"}, 500)

            storage = os.path.join(data_dir, "storage")

            # 附件文件路径解析
            try:
                con = sqlite3.connect(tmp)
                cur = con.cursor()
                akeys = dict(cur.execute("SELECT itemID, key FROM items").fetchall())
                con.close()
            except Exception:
                akeys = {}

            def resolve_file(link_mode, path, att_item_id):
                if not path:
                    return None
                if link_mode == 0 and path.startswith("storage:"):
                    fn = path[len("storage:"):]
                    p = os.path.join(storage, akeys.get(att_item_id, ""), fn)
                    return p if os.path.isfile(p) else None
                if link_mode == 1:  # 链接文件
                    p = path
                    if p.startswith("attachments:"):
                        base = None
                        for p2 in _glob.glob(os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles\*\prefs.js")):
                            txt = open(p2, encoding="utf-8", errors="ignore").read()
                            for ln in txt.splitlines():
                                if "extensions.zotero.baseAttachmentPath" in ln and '", "' in ln:
                                    base = ln.split('", "')[1].rsplit('")')[0].replace("\\\\", "\\")
                        if base:
                            p = os.path.join(base, path[len("attachments:"):])
                        else:
                            return None
                    return p if os.path.isfile(p) else None
                return None

            # 建匹配索引
            lit = load_json(P["lit"], [])
            by_doi, by_title = {}, {}
            for r in lit:
                doi = (r.get("doi") or (r.get("doi_cf") or {}).get("doi") or "").lower().strip()
                if doi:
                    by_doi[doi] = r
                t = (r.get("title") or "").strip()
                if t:
                    by_title[t] = r

            import re as _re
            matched, keyed, unmatched = 0, 0, []
            copied = []
            seen = set()
            for item_id, ikey, itype, doi, title in items:
                if item_id in seen:
                    continue
                # 附件文件
                fpath = None
                for pid, aid, lm, pth, ct in atts:
                    if pid == item_id:
                        f = resolve_file(lm, pth, aid)
                        if f and f.lower().endswith(".pdf"):
                            fpath = f
                            break
                # 匹配平台条目
                dl = (doi or "").lower().strip()
                rec = by_doi.get(dl) if dl else None
                if not rec and title:
                    rec = by_title.get(title.strip())
                if not rec:
                    if title or dl:
                        unmatched.append({"key": ikey, "title": (title or "")[:60], "doi": dl, "has_pdf": bool(fpath)})
                    continue
                seen.add(item_id)
                # 记key
                if not rec.get("zotero_key"):
                    rec["zotero_key"] = ikey
                    keyed += 1
                elif rec.get("zotero_key") != ikey:
                    rec["zotero_key"] = ikey
                # 拷贝文件
                if fpath and not rec.get("pdf_path"):
                    surname = _re.sub(r"[^A-Za-z\- ]", "", (rec.get("authors") or "").split(",")[0].strip()).strip() or "Unknown"
                    fn = _re.sub(r"[\\/:*?\"<>|\r\n]+", " ", f"{rec.get('year') or ''}_{surname}_{(rec.get('journal') or 'J')[:18]}").strip()[:60] or "paper"
                    dst_dir = os.path.join(P["lit_dir"], rec.get("folder", "00_待分类"))
                    os.makedirs(dst_dir, exist_ok=True)
                    dst = os.path.join(dst_dir, fn + ".pdf")
                    n = 1
                    while os.path.exists(dst):
                        dst = os.path.join(dst_dir, f"{fn}_{n}.pdf")
                        n += 1
                    try:
                        shutil.copy2(fpath, dst)
                        rec["pdf_path"] = dst
                        matched += 1
                        copied.append(dst)
                    except Exception as e:
                        unmatched.append({"key": ikey, "title": (title or "")[:50], "err": f"拷贝失败{str(e)[:40]}"})
                elif fpath and rec.get("pdf_path") and not os.path.exists(rec["pdf_path"]):
                    rec["pdf_path"] = fpath  # 原链接失效，改用Zotero的
                    matched += 1

            save_json(P["lit"], lit)
            mark_shared()
            return self._json({"ok": True, "matched": matched, "keys": keyed,
                               "copied": [os.path.basename(c) for c in copied][:20],
                               "unmatched": unmatched[:30], "unmatched_count": len(unmatched)})

        if p == "/api/zotero/attach":
            """把平台PDF挂载为Zotero条目的附件（需Zotero完全关闭；自动备份zotero.sqlite）"""
            ok, _ = zot_ping()
            if ok:
                return self._json({"ok": False, "error": "Zotero正在运行。请先完全退出Zotero（右键任务栏图标退出），再点本按钮。"})
            import glob as _glob
            data_dir = os.path.expandvars(r"%USERPROFILE%\Zotero")
            for p2 in _glob.glob(os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles\*\prefs.js")):
                try:
                    txt = open(p2, encoding="utf-8", errors="ignore").read()
                    for ln in txt.splitlines():
                        if "extensions.zotero.dataDir" in ln and '", "' in ln:
                            v = ln.split('", "')[1].rsplit('")')[0].replace("\\\\", "\\")
                            if os.path.exists(os.path.join(v, "zotero.sqlite")):
                                data_dir = v
                except Exception:
                    pass
            db = os.path.join(data_dir, "zotero.sqlite")
            if not os.path.exists(db):
                return self._json({"ok": False, "error": "未找到 zotero.sqlite"}, 404)
            # 备份
            bak = db + ".bak_platform_" + time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(db, bak)
            for ext in ("-wal", "-shm"):
                try:
                    shutil.copy2(db + ext, bak + ext)
                except Exception:
                    pass
            lit = load_json(P["lit"], [])
            # 0) 补查key：Zotero关闭后DB为最终状态，异步保存此时必然可见
            prelooked = 0
            for rec in lit:
                if rec.get("zotero_key") or not (rec.get("doi") or rec.get("title")):
                    continue
                doi = rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or ""
                key = zot_db_find(doi or None, rec.get("title") or None)
                if key:
                    rec["zotero_key"] = key
                    prelooked += 1
            con = sqlite3.connect(db, timeout=30)
            cur = con.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            import string as _string
            import random as _random
            ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

            def new_key():
                return "".join(_random.choice(ALPHABET) for _ in range(8))

            att_type = cur.execute("SELECT itemTypeID FROM itemTypes WHERE typeName='attachment'").fetchone()[0]
            title_fid = cur.execute("SELECT fieldID FROM fields WHERE fieldName='title'").fetchone()
            title_fid = title_fid[0] if title_fid else None
            lib_id = cur.execute("SELECT DISTINCT libraryID FROM items LIMIT 1").fetchone()[0]
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            attached, skipped, errs = 0, 0, []

            for rec in lit:
                if not (rec.get("zotero_key") and rec.get("pdf_path") and os.path.exists(rec["pdf_path"])):
                    continue
                # 自愈式：以数据库实际附件状态为准，不依赖历史标志
                prow = cur.execute("SELECT itemID, libraryID FROM items WHERE key=?", (rec["zotero_key"],)).fetchone()
                if not prow:
                    continue
                parent_id = prow[0]
                lib_id = prow[1]
                has = cur.execute(
                    """SELECT COUNT(*) FROM itemAttachments a JOIN items i ON a.itemID=i.itemID
                       WHERE a.parentItemID=? AND a.contentType='application/pdf'""", (parent_id,)).fetchone()[0]
                if has:
                    rec["zotero_attached"] = "existing"
                    skipped += 1
                    continue
                try:
                    akey = new_key()
                    cur.execute(
                        "INSERT INTO items (libraryID, itemTypeID, key, dateAdded, dateModified, clientDateModified, version, synced) VALUES (?,?,?,?,?,?,0,0)",
                        (lib_id, att_type, akey, now, now, now))
                    att_id = cur.lastrowid
                    fn = os.path.basename(rec["pdf_path"])
                    cur.execute(
                        "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path, syncState) VALUES (?,?,0,'application/pdf',?,0)",
                        (att_id, parent_id, "storage:" + fn))
                    if title_fid:
                        vrow = cur.execute("SELECT valueID FROM itemDataValues WHERE value=?", ("PDF 全文",)).fetchone()
                        if vrow:
                            vid = vrow[0]
                        else:
                            cur.execute("INSERT INTO itemDataValues (value) VALUES (?)", ("PDF 全文",))
                            vid = cur.lastrowid
                        cur.execute("INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?,?,?)", (att_id, title_fid, vid))
                    sdir = os.path.join(data_dir, "storage", akey)
                    os.makedirs(sdir, exist_ok=True)
                    shutil.copy2(rec["pdf_path"], os.path.join(sdir, fn))
                    con.commit()
                    rec["zotero_attached"] = True
                    attached += 1
                except Exception as e:
                    con.rollback()
                    errs.append(f"[{rec.get('orig_num')}] {str(e)[:60]}")
            con.commit()
            con.close()
            save_json(P["lit"], lit)
            return self._json({"ok": True, "attached": attached, "skipped_existing": skipped,
                               "prelooked_keys": prelooked,
                               "errors": errs[:10], "backup": bak})

        if p == "/api/zotero/close":
            """关闭Zotero（挂载PDF前置步骤）"""
            ok, _ = zot_ping()
            if ok:
                os.system("taskkill /IM zotero.exe /F >nul 2>&1")
                for _ in range(10):
                    time.sleep(1)
                    if not zot_ping():
                        break
            return self._json({"ok": not zot_ping()})

        if p == "/api/zotero/launch":
            """启动Zotero并等待connector就绪"""
            ok, ver = zot_ensure_running(timeout=45)
            return self._json({"ok": ok, "version": ver})

        if p == "/api/zotero/ping":
            ok, ver = zot_ping()
            return self._json({"ok": ok, "version": ver})

        if p == "/api/zotero/import":
            """录入单条: {id} 或 {num}。先查重（数据库反查），已存在只记key不写入"""
            lit = load_json(P["lit"], [])
            rec = None
            for r in lit:
                if data.get("id") and r.get("id") == data["id"]:
                    rec = r
                    break
                if data.get("num") is not None and r.get("orig_num") == data["num"]:
                    rec = r
                    break
            if not rec:
                return self._json({"ok": False, "error": "条目不存在"}, 404)
            doi = rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or ""
            title = rec.get("title") or ""
            # 1) 查重：库里已有 -> 只记key
            key = zot_db_find(doi or None, title or None)
            if key:
                rec["zotero_key"] = key
                save_json(P["lit"], lit)
                return self._json({"ok": True, "key": key, "existed": True})
            # 2) 不存在 -> 确保运行并写入
            ok, ver = zot_ensure_running()
            if not ok:
                return self._json({"ok": False, "error": "Zotero未运行且启动超时（请手动打开Zotero后重试）"})
            try:
                zot_save_item(rec)
            except Exception as e:
                return self._json({"ok": False, "error": f"Zotero写入失败: {str(e)[:120]}"})
            key = None
            for _ in range(2):
                time.sleep(1.5)
                key = zot_db_find(doi or None, title or None)
                if key:
                    break
            rec["zotero_key"] = key
            save_json(P["lit"], lit)
            return self._json({"ok": True, "key": key, "existed": False, "has_pdf": bool(rec.get("pdf_path"))})

        if p == "/api/zotero/open":
            """在Zotero中打开并选中: {id}。未录入则先录入"""
            lit = load_json(P["lit"], [])
            rec = None
            for r in lit:
                if data.get("id") and r.get("id") == data["id"]:
                    rec = r
                    break
                if data.get("num") is not None and r.get("orig_num") == data["num"]:
                    rec = r
                    break
            if not rec:
                return self._json({"ok": False, "error": "条目不存在"}, 404)
            if not rec.get("zotero_key"):
                doi = rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or ""
                key = zot_db_find(doi or None, rec.get("title") or None)
                if key:
                    rec["zotero_key"] = key
                    save_json(P["lit"], lit)
            if not rec.get("zotero_key"):
                ok, ver = zot_ensure_running()
                if not ok:
                    return self._json({"ok": False, "error": "Zotero未运行且启动超时"})
                try:
                    zot_save_item(rec)
                    doi = rec.get("doi") or (rec.get("doi_cf") or {}).get("doi") or ""
                    for _ in range(10):
                        time.sleep(2)
                        rec["zotero_key"] = zot_db_find(doi or None, rec.get("title") or None)
                        if rec["zotero_key"]:
                            break
                    save_json(P["lit"], lit)
                except Exception as e:
                    return self._json({"ok": False, "error": f"录入失败: {str(e)[:120]}"})
            key = rec.get("zotero_key")
            if key:
                try:
                    # 优先直达PDF阅读器；无附件时退回选中条目
                    att = rec.get("zotero_att_key")
                    if not att:
                        found = zot_att_find(key)
                        if found:
                            rec["zotero_att_key"], rec["_zot_lib"] = found[0], found[1]
                            save_json(P["lit"], lit)
                            att = found[0]
                    if att:
                        lib = rec.get("_zot_lib") or 1
                        prefix = "library" if lib == 1 else f"groups/{lib}"
                        os.startfile(f"zotero://open-pdf/{prefix}/items/{att}")
                        return self._json({"ok": True, "key": key, "att_key": att, "mode": "pdf"})
                    os.startfile(f"zotero://select/library/items/{key}")
                    return self._json({"ok": True, "key": key, "mode": "select"})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:100]}, 500)
            try:
                os.startfile("zotero://open-library")
                return self._json({"ok": True, "key": None, "note": "未定位到条目，已打开Zotero"})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:100]}, 500)

        if p == "/api/open_folder":
            """资源管理器定位文件: {path}"""
            path = (data.get("path") or "").strip()
            if not os.path.exists(path):
                return self._json({"ok": False, "error": "文件不存在"}, 404)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return self._json({"ok": True})

        if p == "/api/pdf/optimize":
            """批量压缩优化: {folder: 可选分类名, 空为全部}"""
            import fitz  # noqa: F401 确认可用
            folder = data.get("folder") or ""
            lit = load_json(P["lit"], [])
            targets = []
            seen = set()
            for r in lit:
                pp = r.get("pdf_path")
                if not pp or not os.path.exists(pp) or pp in seen:
                    continue
                if folder and r.get("folder") != folder:
                    continue
                seen.add(pp)
                targets.append(pp)
            results, total_b, total_a, fails = [], 0, 0, 0
            for pp in targets:
                r = pdf_optimize_one(pp)
                if r:
                    before, after = r
                    total_b += before
                    total_a += after
                    results.append({"file": os.path.basename(pp), "before": before, "after": after,
                                    "saved_pct": round((before - after) * 100.0 / max(before, 1), 1)})
                else:
                    fails += 1
            return self._json({"ok": True, "count": len(results), "fails": fails,
                               "before": total_b, "after": total_a,
                               "saved": total_b - total_a,
                               "results": sorted(results, key=lambda x: -x["before"])[:40]})

        if p == "/api/pdf/stats":
            lit = load_json(P["lit"], [])
            files = [(r["pdf_path"], os.path.getsize(r["pdf_path"])) for r in lit
                     if r.get("pdf_path") and os.path.exists(r["pdf_path"])]
            files.sort(key=lambda x: -x[1])
            return self._json({"ok": True, "count": len(files),
                               "total": sum(s for _, s in files),
                               "top": [{"file": os.path.basename(p), "size": s} for p, s in files[:10]]})

        if p == "/api/pdf/merge":
            """合并: {paths: [...], out_name}"""
            paths = [x for x in (data.get("paths") or []) if os.path.exists(x)]
            if len(paths) < 2:
                return self._json({"ok": False, "error": "至少需要两个存在的PDF路径"}, 400)
            try:
                import fitz
                out_dir = os.path.join(P["lit_dir"], "00_待分类")
                os.makedirs(out_dir, exist_ok=True)
                out = os.path.join(out_dir, (data.get("out_name") or "合并.pdf").rstrip(".pdf") + ".pdf")
                doc = fitz.open()
                for pp in paths:
                    d = fitz.open(pp)
                    doc.insert_pdf(d)
                    d.close()
                doc.save(out, garbage=4, deflate=True)
                doc.close()
                return self._json({"ok": True, "out": out, "size": os.path.getsize(out)})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:120]}, 500)

        if p == "/api/pdf/extract":
            """提取页: {path, spec:'1-3,5', out_name}"""
            src = data.get("path") or ""
            if not os.path.exists(src):
                return self._json({"ok": False, "error": "源文件不存在"}, 404)
            try:
                import fitz
                doc = fitz.open(src)
                specs = pdf_pagespec(data.get("spec") or "1", doc.page_count)
                if not specs:
                    return self._json({"ok": False, "error": "页码范围无效"}, 400)
                new = fitz.open()
                for a, b in specs:
                    new.insert_pdf(doc, from_page=a - 1, to_page=b - 1)
                out_dir = os.path.join(P["lit_dir"], "00_待分类")
                os.makedirs(out_dir, exist_ok=True)
                out = os.path.join(out_dir, (data.get("out_name") or "提取.pdf").rstrip(".pdf") + ".pdf")
                new.save(out, garbage=4, deflate=True)
                new.close()
                doc.close()
                return self._json({"ok": True, "out": out, "size": os.path.getsize(out)})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:120]}, 500)

        if p == "/api/tr/health":
            h = tr_ping()
            if not h and not tr_ensure():
                return self._json({"ok": False, "error": "翻译服务启动失败"}, 503)
            return self._json(tr_ping() or {"ok": False})

        if p == "/api/tr/create":
            """从本地PDF创建翻译任务: {path, skip_refs}"""
            path = (data.get("path") or "").strip().strip('"')
            if not os.path.exists(path):
                return self._json({"ok": False, "error": "PDF不存在: " + path}, 404)
            if not tr_ensure():
                return self._json({"ok": False, "error": "翻译服务不可用"}, 503)
            try:
                job = tr_create_job(path, bool(data.get("skip_refs", True)))
                return self._json(job)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:150]}, 500)

        if p == "/api/tr/jobs":
            if not tr_ensure():
                return self._json({"ok": False, "error": "翻译服务不可用"}, 503)
            try:
                return self._json(tr_call("GET", "/api/jobs"))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:100]}, 500)

        if p == "/api/tr/job":
            jid = (data.get("jid") or "").strip()
            try:
                return self._json(tr_call("GET", f"/api/jobs/{jid}"))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:100]}, 500)

        if p == "/api/tr/delete":
            jid = (data.get("jid") or "").strip()
            try:
                return self._json(tr_call("DELETE", f"/api/jobs/{jid}"))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:100]}, 500)

        if p == "/api/tr/attach_zh":
            """把已完成翻译任务的译文存为该条目同目录的 _中文版.pdf（不新建条目）: {id}"""
            lit = load_json(P["lit"], [])
            rec = None
            for r in lit:
                if (data.get("id") and r.get("id") == data["id"]) or \
                   (data.get("num") is not None and r.get("orig_num") == data["num"]):
                    rec = r
                    break
            if not rec or not (rec.get("pdf_path") and os.path.exists(rec["pdf_path"])):
                return self._json({"ok": False, "error": "条目不存在或无本地PDF"}, 404)
            jid = rec.get("tr_jid") or (data.get("jid") or "")
            if not jid:
                return self._json({"ok": False, "error": "该条目没有翻译任务"}, 400)
            try:
                req2 = urllib.request.Request(f"{TR}/api/jobs/{jid}/download")
                with urllib.request.urlopen(req2, timeout=120) as r2:
                    pdf = r2.read()
                if not pdf.startswith(b"%PDF-"):
                    return self._json({"ok": False, "error": "译文尚未生成"}, 400)
                stem = os.path.splitext(os.path.basename(rec["pdf_path"]))[0]
                dst = os.path.join(os.path.dirname(rec["pdf_path"]), stem + "_中文版.pdf")
                k = 2
                while os.path.exists(dst):
                    dst = os.path.join(os.path.dirname(rec["pdf_path"]), f"{stem}_中文版_{k}.pdf")
                    k += 1
                with open(dst, "wb") as f:
                    f.write(pdf)
                rec["tr_done"] = True
                save_json(P["lit"], lit)
                return self._json({"ok": True, "path": dst})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:150]}, 500)

        if p == "/api/tr/saveback":
            """译文PDF回存文献库: {jid, folder, title, authors, journal, year, doi}"""
            jid = (data.get("jid") or "").strip()
            folder = data.get("folder") or "00_待分类"
            try:
                req2 = urllib.request.Request(f"{TR}/api/jobs/{jid}/download")
                with urllib.request.urlopen(req2, timeout=120) as r2:
                    pdf = r2.read()
                if not pdf.startswith(b"%PDF-"):
                    return self._json({"ok": False, "error": "译文尚未生成"}, 400)
                jobs = tr_call("GET", "/api/jobs")
                jname = ""
                for j in jobs if isinstance(jobs, list) else []:
                    if j.get("id") == jid:
                        jname = j.get("filename") or ""
                        break
                base = os.path.splitext(jname)[0] if jname else "translated"
                dst_dir = os.path.join(P["lit_dir"], folder)
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, (base + "_中文版.pdf")[:120])
                i = 1
                while os.path.exists(dst):
                    dst = os.path.join(dst_dir, f"{base}_中文版_{i}.pdf")
                    i += 1
                with open(dst, "wb") as f:
                    f.write(pdf)
                lit = load_json(P["lit"], [])
                num = next_import_id(lit)
                lit.append({
                    "id": f"imp{int(time.time())}_{num}", "orig_num": num,
                    "authors": data.get("authors") or base[:30],
                    "title": (data.get("title") or base + "（中文翻译版）")[:200],
                    "journal": data.get("journal") or "", "year": data.get("year"),
                    "doi": data.get("doi") or "", "raw": "",
                    "chapter": data.get("chapter") or "", "folder": folder,
                    "source": "论文翻译（" + (data.get("orig_src") or "翻译工具") + "）",
                    "verified": False, "pdf_path": dst, "note": "机器翻译版，仅供精读参考",
                })
                save_json(P["lit"], lit)
                mark_shared()
                return self._json({"ok": True, "path": dst, "num": num})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:150]}, 500)

        if p == "/api/import/scan":
            """扫描文件夹中的PDF（可预览不入库）: {root, recursive}"""
            root = (data.get("root") or P["lit_dir"]).strip().strip('"')
            if not os.path.isdir(root):
                return self._json({"ok": False, "error": "目录不存在: " + root}, 404)
            lit = load_json(P["lit"], [])
            known = {os.path.normpath(r.get("pdf_path", "")) for r in lit if r.get("pdf_path")}
            found = []
            for dirpath, dirs, files in os.walk(root):
                if not data.get("recursive", True) and dirpath != root:
                    continue
                for f in sorted(files):
                    if not f.lower().endswith(".pdf"):
                        continue
                    fp = os.path.normpath(os.path.join(dirpath, f))
                    if fp in known or "_中文版" in f or f.startswith("._"):
                        continue
                    found.append(fp)
            found = found[:int(data.get("limit", 30))]
            items = []
            for fp in found:
                meta = pdf_meta_extract(fp)
                meta["suggest"] = auto_classify(meta["title"])
                items.append(meta)
            return self._json({"ok": True, "count": len(items), "items": items})

        if p == "/api/import/ris":
            items = parse_ris(data.get("text") or "")
            for it in items:
                it["suggest"] = auto_classify(it.get("title"))
            return self._json({"ok": True, "count": len(items), "items": items})

        if p == "/api/import/commit":
            """确认入库: {items: [{title,authors,journal,year,doi,folder,pdf_path,source}]}"""
            lit = load_json(P["lit"], [])
            num = next_import_id(lit)
            added = 0
            known_paths = {os.path.normpath(r.get("pdf_path", "")) for r in lit if r.get("pdf_path")}
            for it in data.get("items") or []:
                if not (it.get("title") or it.get("doi") or it.get("pdf_path")):
                    continue
                if it.get("pdf_path") and os.path.normpath(it["pdf_path"]) in known_paths:
                    continue
                lit.append({
                    "id": f"imp{int(time.time()*1000)}_{num}", "orig_num": num,
                    "authors": it.get("authors") or "", "title": (it.get("title") or "未命名")[:200],
                    "journal": it.get("journal") or "", "year": it.get("year"),
                    "doi": it.get("doi") or "", "raw": "",
                    "chapter": it.get("chapter") or "", "folder": it.get("folder") or "00_待分类",
                    "source": it.get("source") or "批量导入", "verified": False,
                    "pdf_path": it.get("pdf_path") or "", "note": it.get("note") or "",
                })
                num += 1
                added += 1
            save_json(P["lit"], lit)
            mark_shared()
            return self._json({"ok": True, "added": added, "total": len(lit)})

        # ---------------- 多论文项目管理 ----------------
        if p == "/api/project/switch":
            pid = (data.get("pid") or "").strip()
            d = load_projects()
            if not any(e["id"] == pid for e in d["projects"]):
                return self._json({"ok": False, "error": "项目不存在"}, 404)
            d["active"] = pid
            save_projects(d)
            return self._json({"ok": True, "pid": pid})

        if p == "/api/project/create":
            """新建论文项目: {name, type, benchmark_pdf, ai}。AI拆解对标论文生成拆解报告+大纲+章节"""
            name = (data.get("name") or "").strip()
            if not name:
                return self._json({"ok": False, "error": "请填写论文名"}, 400)
            ptype = (data.get("type") or "综述").strip()
            bpdf = (data.get("benchmark_pdf") or "").strip().strip('"')
            use_ai = bool(data.get("ai", True)) and bpdf and os.path.exists(bpdf)
            d = load_projects()
            pid = "pj" + time.strftime("%m%d%H%M%S")
            pdir = os.path.join(PROJ_DIR, pid)
            os.makedirs(os.path.join(pdir, "成稿"), exist_ok=True)
            os.makedirs(os.path.join(pdir, "导出"), exist_ok=True)
            lit_dir = os.path.join(LIT_ROOT, _safe_name(name))
            dec_err, analysis_md, chapters, outline, folders = "", "", None, None, None
            if use_ai:
                try:
                    r = decompose_benchmark(bpdf, name, ptype)
                    analysis_md = r["analysis"]
                    chapters, outline, folders = r["chapters"], r["outline"], r["folders"]
                except Exception as e:
                    dec_err = str(e)[:200]
            if not chapters:
                chapters = json.loads(json.dumps(DEFAULT_CHAPTERS))
            if not folders:
                folders = list(DEFAULT_FOLDERS)
            if "00_对标论文" not in folders:
                folders.insert(0, "00_对标论文")
            if not outline:
                outline = [{"chapter": cid, "title": v[1] + "（待细化）", "words": v[2],
                            "refs": 0, "status": "未开始", "points": ""} for cid, v in chapters.items()]
            entry = {"id": pid, "name": name, "type": ptype, "created": time.strftime("%Y-%m-%d"),
                     "legacy": False, "chapters": chapters, "folders": folders, "lit_dir": lit_dir}
            bm_meta = {}
            if bpdf and os.path.exists(bpdf):
                bm_meta = pdf_meta_extract(bpdf)
                entry["benchmark"] = {"title": bm_meta.get("title") or os.path.basename(bpdf),
                                      "pdf_path": os.path.abspath(bpdf)}
            # 落盘骨架
            with open(os.path.join(pdir, "拆解.md"), "w", encoding="utf-8") as f:
                f.write(analysis_md or ("# 拆解报告\n\n（尚未生成" +
                        ("：AI拆解失败——" + dec_err if dec_err else "（未提供对标PDF）") +
                        "。可在「对标拆解」页点「重新AI拆解」。）\n"))
            save_json(os.path.join(pdir, "outline.json"), outline)
            save_json(os.path.join(pdir, "figures_plan.json"), [])
            lit0 = []
            if entry.get("benchmark"):
                lit0.append({"id": "bm_" + pid, "orig_num": 1,
                             "authors": bm_meta.get("authors") or "", "title": bm_meta.get("title") or name,
                             "journal": bm_meta.get("journal") or "", "year": bm_meta.get("year"),
                             "doi": bm_meta.get("doi") or "", "raw": "", "chapter": "",
                             "folder": "00_对标论文", "source": "对标论文（项目创建）",
                             "verified": False, "pdf_path": bm_meta.get("pdf_path") or os.path.abspath(bpdf),
                             "note": "对标参考的优秀论文"})
            save_json(os.path.join(pdir, "literature.json"), lit0)
            for fd in folders:
                os.makedirs(os.path.join(lit_dir, fd), exist_ok=True)
            d["projects"].append(entry)
            d["active"] = pid
            save_projects(d)
            mark_shared()
            return self._json({"ok": True, "pid": pid, "name": name, "decomposed": bool(analysis_md),
                               "analysis_chars": len(analysis_md), "chapters": list(chapters),
                               "folders": folders, "lit_entries": len(lit0), "error": dec_err})

        if p == "/api/project/decompose":
            """重跑AI拆解: {pdf} 可选新PDF。更新拆解.md；overwrite=true时同步覆盖章节/大纲/文件夹"""
            d = load_projects()
            entry = next((e for e in d["projects"] if e["id"] == d.get("active")), d["projects"][0])
            if entry.get("legacy"):
                return self._json({"ok": False, "error": "内置综述项目使用既有拆解报告，无需AI拆解"}, 400)
            bpdf = (data.get("pdf") or (entry.get("benchmark") or {}).get("pdf_path") or "").strip().strip('"')
            if not (bpdf and os.path.exists(bpdf)):
                return self._json({"ok": False, "error": "未找到对标PDF，请先在项目里指定"}, 400)
            try:
                r = decompose_benchmark(bpdf, entry["name"], entry.get("type") or "综述")
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:200]}, 500)
            P = proj_paths(entry)
            with open(P["analysis"], "w", encoding="utf-8") as f:
                f.write(r["analysis"])
            if data.get("overwrite"):
                entry["chapters"] = r["chapters"]
                folders = r["folders"] or list(DEFAULT_FOLDERS)
                if "00_对标论文" not in folders:
                    folders.insert(0, "00_对标论文")
                entry["folders"] = folders
                save_projects(d)
                save_json(P["outline"], r["outline"] or [])
                for fd in folders:
                    os.makedirs(os.path.join(P["lit_dir"], fd), exist_ok=True)
            else:
                save_projects(d)
            return self._json({"ok": True, "analysis_chars": len(r["analysis"]),
                               "overwrite": bool(data.get("overwrite"))})

        if p == "/api/project/update":
            """更新项目: {pid, name?, chapters?:[{id,stem,title,target}], folders?:[]}"""
            d = load_projects()
            entry = next((e for e in d["projects"] if e["id"] == data.get("pid")), None)
            if not entry:
                return self._json({"ok": False, "error": "项目不存在"}, 404)
            if data.get("name") and data["name"].strip():
                entry["name"] = data["name"].strip()
            if data.get("chapters"):
                newc = {}
                for i, c in enumerate(data["chapters"]):
                    cid = (c.get("id") or f"ch{i + 1}").strip()
                    if entry.get("legacy"):
                        old = entry.get("chapters", {}).get(cid) or CHAPTER_FILES.get(cid)
                        stem = old[0]
                    else:
                        stem = re.sub(r'[\\/:*?"<>| ]+', "_", c.get("stem") or cid)[:30]
                    newc[cid] = [stem, (c.get("title") or cid)[:40], int(c.get("target") or 0)]
                entry["chapters"] = newc
            if data.get("folders") and not entry.get("legacy"):
                entry["folders"] = [f for f in data["folders"] if f and f.strip()]
                for fd in entry["folders"]:
                    os.makedirs(os.path.join(proj_paths(entry)["lit_dir"], fd), exist_ok=True)
            save_projects(d)
            return self._json({"ok": True})

        if p == "/api/project/delete":
            """删除项目（移入 论文项目/_回收/ 可恢复；内置综述不可删）"""
            pid = (data.get("pid") or "").strip()
            d = load_projects()
            entry = next((e for e in d["projects"] if e["id"] == pid), None)
            if not entry:
                return self._json({"ok": False, "error": "项目不存在"}, 404)
            if entry.get("legacy"):
                return self._json({"ok": False, "error": "内置综述项目不可删除"}, 400)
            pdir = os.path.join(PROJ_DIR, pid)
            if os.path.isdir(pdir):
                trash = os.path.join(PROJ_DIR, "_回收")
                os.makedirs(trash, exist_ok=True)
                shutil.move(pdir, os.path.join(trash, pid + "_" + time.strftime("%Y%m%d_%H%M%S")))
            d["projects"] = [e for e in d["projects"] if e["id"] != pid]
            if d.get("active") == pid or d.get("active") not in {e["id"] for e in d["projects"]}:
                d["active"] = d["projects"][0]["id"]
            save_projects(d)
            return self._json({"ok": True, "active": d["active"]})

        if p == "/api/export":
            lang = data.get("lang", "zh")
            outline = load_json(P["outline"], [])
            merged = []
            front = os.path.join(P["ch"], "00_摘要.md")
            if lang == "zh" and os.path.exists(front):
                with open(front, encoding="utf-8") as f:
                    merged.append(f.read().strip())
            for cid in P["chapters"]:
                fp = ch_path(cid, lang, P)
                if os.path.exists(fp):
                    with open(fp, encoding="utf-8") as f:
                        merged.append(f.read().strip())
            refs_path = os.path.join(P["ch"], "参考文献.md")
            if os.path.exists(refs_path):
                with open(refs_path, encoding="utf-8") as f:
                    refs_text = f.read()
                if lang == "en":
                    refs_text = refs_text.replace("# 参考文献", "# References", 1)
                merged.append(refs_text.strip())
            full = "\n\n---\n\n".join(merged)
            os.makedirs(P["export"], exist_ok=True)
            name = P["exp_zh"] if lang == "zh" else P["exp_en"]
            md_path = os.path.join(P["export"], name + ".md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(full)
            html = md_to_html(full, name, lang)
            html_path = os.path.join(P["export"], name + ".html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            docx_path = ""
            try:
                docx_path = export_docx(full, os.path.join(P["export"], name + ".docx"), name)
            except Exception as e:
                docx_path = f"python-docx 不可用（{e}），已提供 .html 可用 Word 打开另存为 .docx"
            return self._json({"ok": True, "md": md_path, "html": html_path, "docx": docx_path,
                               "words_zh": count_words(full)[0], "words_en": count_words(full)[1]})
        return self._json({"error": "not found"}, 404)


# ---------------- 极简 Markdown -> HTML（导出用） ----------------
def md_to_html(md, title, lang):
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = md.splitlines()
    out, in_list, in_para = [], False, False

    def close_lists():
        nonlocal in_list, in_para
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_para:
            out.append("</p>")
            in_para = False

    for ln in lines:
        s = ln.rstrip()
        if not s.strip():
            close_lists()
            continue
        h = re.match(r"^(#{1,4})\s+(.*)", s)
        if h:
            close_lists()
            lvl = len(h.group(1))
            out.append(f"<h{lvl}>{esc(h.group(2))}</h{lvl}>")
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            close_lists()
            out.append("<table><tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr></table>")
            continue
        m = re.match(r"^[-*]\s+(.*)", s)
        if m:
            if in_para:
                out.append("</p>")
                in_para = False
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc(m.group(1))}</li>")
            continue
        t = esc(s)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
        if not in_para:
            out.append("<p>")
            in_para = True
        out.append(t + "<br/>")
    close_lists()
    body = "\n".join(out)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>body{{font-family:'Times New Roman',SimSun,serif;max-width:800px;margin:40px auto;line-height:1.8;font-size:14pt}}
h1,h2,h3,h4{{font-family:SimHei,'Microsoft YaHei',sans-serif}}
table{{border-collapse:collapse;width:100%}}td{{border:1px solid #999;padding:6px;font-size:11pt}}</style>
</head><body>{body}</body></html>"""


def export_docx(md, path, title):
    from docx import Document
    doc = Document()
    for para_lines in md.split("\n\n"):
        for ln in para_lines.splitlines():
            if not ln.strip():
                continue
            if ln.startswith("#### "):
                doc.add_heading(ln[5:], level=4)
            elif ln.startswith("### "):
                doc.add_heading(ln[4:], level=3)
            elif ln.startswith("## "):
                doc.add_heading(ln[3:], level=2)
            elif ln.startswith("# "):
                doc.add_heading(ln[2:], level=1)
            elif ln.startswith("|"):
                continue
            else:
                doc.add_paragraph(ln)
    doc.save(path)
    return path


def main():
    port = int(os.environ.get("PORT") or 8320)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"综述平台已启动: {url}  （Ctrl+C 退出）")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
