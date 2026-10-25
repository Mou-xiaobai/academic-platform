# -*- coding: utf-8 -*-
"""翻译调度：批处理、并发、术语表、JSON 契约校验、缓存、失败补译。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

LANG_NAME = {"zh": "简体中文", "en": "English"}
FENCE_OPEN = re.compile(r"^\x60{3}[a-zA-Z]*\s*")
FENCE_CLOSE = re.compile(r"\s*\x60{3}\s*$")


class TranslateError(Exception):
    pass


def _extract_json(s: str) -> dict:
    s = s.strip()
    s = FENCE_OPEN.sub("", s)
    s = FENCE_CLOSE.sub("", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no json object")
    body = s[i:j + 1]
    try:
        return json.loads(body)
    except Exception:
        body2 = re.sub(r",\s*([}\]])", r"\1", body)
        return json.loads(body2)


class Translator:
    def __init__(self, settings: dict, api_key: str):
        self.key = api_key
        self.base = (settings.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.model = settings.get("model") or "deepseek-chat"
        self.concurrency = int(settings.get("concurrency") or 4)
        self.batch_chars = int(settings.get("batch_chars") or 2600)
        self.stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cache_hits": 0,
                      "retries": 0, "failed_batches": 0}
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self.cache_path = settings.get("_cache_path")
        self.cache = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _chat(self, messages: list, temperature: float = 0.6, max_tokens: int = 8192,
              timeout: int = 180) -> str:
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.key})
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.loads(r.read().decode("utf-8"))
                usage = d.get("usage") or {}
                with self._lock:
                    self.stats["calls"] += 1
                    self.stats["tokens_in"] += usage.get("prompt_tokens", 0)
                    self.stats["tokens_out"] += usage.get("completion_tokens", 0)
                return d["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")[:300]
                except Exception:
                    pass
                last = TranslateError("HTTP %s: %s" % (e.code, body))
                if e.code in (400, 401, 402, 403):
                    raise last
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                last = TranslateError("%s: %s" % (type(e).__name__, e))
                time.sleep(1.5 * (attempt + 1))
            with self._lock:
                self.stats["retries"] += 1
        raise last or TranslateError("unknown error")

    def _system_prompt(self, src: str, tgt: str, glossary: dict) -> str:
        gl = "\n".join("- %s → %s" % (k, v) for k, v in glossary.items())
        src_name = LANG_NAME.get(src, src)
        tgt_name = LANG_NAME.get(tgt, tgt)
        rules = [
            "你是一台专业的学术文献翻译引擎，把%s文本翻译成%s。" % (src_name, tgt_name),
            "输出规则：",
            "1. 只输出一个 JSON 对象，形如 {\"p1b0\":\"译文\", ...}；键必须与输入 id 完全一致；"
            "不要输出任何解释，不要用代码块包裹，不要换行。",
            "2. 学术语体，准确、简洁、通顺；不漏译、不扩写、不加评论、不合并条目。",
            "3. 保留原样：文献编号 [12]、[9–11,16]、公式与化学式、数字、单位、缩写、"
            "仪器型号、人名、机构名、URL、DOI、基金号。",
            "4. 图表引用改写：Fig. 3b-d 写作 图3b-d；Figs. 4a and 4b 写作 图4a和图4b；Table 2 写作 表2。",
            "5. 缩写首次出现写「中文全称（ABBR）」，后文只用缩写；原文已有括号缩写的保持不动。",
            "6. 输入是版面切分产生的段落片段，可能从句子中间开始或结束：如实翻译，不要补全上下文。",
            "7. 单条译文内禁止换行；禁止使用 Markdown 语法（星号、井号、反引号等）。",
            "8. 中文译文使用全角标点（，。；：（）），公式与缩写内部保持半角。",
        ]
        if gl:
            rules.append("术语表（必须遵守）：\n" + gl)
        return "\n".join(rules)

    def _user_prompt(self, items: list) -> str:
        payload = {"items": [{"id": it["id"], "text": it["text"]} for it in items]}
        return json.dumps(payload, ensure_ascii=False) + "\n\n请只返回 JSON 对象 {id: 译文}，不要任何其它文字。"

    def translate_units(self, units: list, src: str, tgt: str, glossary: dict,
                        on_progress=None, should_stop=None) -> dict:
        by_text, results = {}, {}
        for u in units:
            by_text.setdefault(u["text"], []).append(u["id"])

        todo = []
        for text, ids in by_text.items():
            h = hashlib.sha1(text.encode("utf-8")).hexdigest()
            if h in self.cache:
                for i in ids:
                    results[i] = self.cache[h]
                self.stats["cache_hits"] += 1
            else:
                todo.append({"ids": ids, "text": text, "hash": h})

        total_chars = max(1, sum(len(t["text"]) for t in todo))
        done_chars = 0
        batches, cur, cur_chars = [], [], 0
        for t in todo:
            if cur and (cur_chars + len(t["text"]) > self.batch_chars or len(cur) >= 24):
                batches.append(cur)
                cur, cur_chars = [], 0
            cur.append(t)
            cur_chars += len(t["text"])
        if cur:
            batches.append(cur)

        if on_progress:
            on_progress(0, len(batches), total_chars, done_chars, dict(self.stats))

        sys_prompt = self._system_prompt(src, tgt, glossary)
        failed = []

        def run_batch(batch, depth=0):
            items = [{"id": b["ids"][0], "text": b["text"]} for b in batch]
            msgs = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": self._user_prompt(items)}]
            try:
                raw = self._chat(msgs, temperature=0.6 if depth == 0 else 0.2)
                data = _extract_json(raw)
            except Exception:
                if len(batch) > 1 and depth < 2:
                    mid = len(batch) // 2
                    return run_batch(batch[:mid], depth + 1) + run_batch(batch[mid:], depth + 1)
                with self._lock:
                    self.stats["failed_batches"] += 1
                return [None for _ in batch]
            out = []
            for b in batch:
                val = data.get(b["ids"][0])
                out.append(val.strip() if isinstance(val, str) and val.strip() else None)
            return out

        def worker(index, batch):
            nonlocal done_chars
            if should_stop and should_stop():
                return
            vals = run_batch(batch)
            for b, v in zip(batch, vals):
                if v is None:
                    failed.append(b)
                else:
                    for bid in b["ids"]:
                        results[bid] = v
                    with self._cache_lock:
                        self.cache[b["hash"]] = v
            done_chars += sum(len(b["text"]) for b in batch)
            if on_progress:
                on_progress(index + 1, len(batches), total_chars, done_chars, dict(self.stats))

        if batches:
            with ThreadPoolExecutor(max_workers=max(1, self.concurrency)) as pool:
                futs = [pool.submit(worker, i, b) for i, b in enumerate(batches)]
                for f in as_completed(futs):
                    f.result()

        for b in failed:
            try:
                raw = self._chat(
                    [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": self._user_prompt([{"id": b["ids"][0], "text": b["text"]}])
                      + "\n\n只翻译这一条，返回一个只含该 id 的 JSON 对象。"}],
                    temperature=0.2, timeout=120)
                data = _extract_json(raw)
                v = data.get(b["ids"][0])
                if isinstance(v, str) and v.strip():
                    for bid in b["ids"]:
                        results[bid] = v.strip()
                    with self._cache_lock:
                        self.cache[b["hash"]] = v.strip()
            except Exception:
                with self._lock:
                    self.stats["failed_batches"] += 1

        if self.cache_path:
            try:
                self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        return results
