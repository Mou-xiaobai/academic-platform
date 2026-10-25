/* 无钴富锂锰综述平台 · 前端逻辑（原生JS，无依赖） */
"use strict";

let DATA = null;           // /api/data 全量缓存
let view = "dash";
let curCh = "ch1", curLang = "zh";
let litFilter = { chapter: "", folder: "", q: "", onlyNoPdf: false, onlyShared: false };
let saveTimer = null;

const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const CH_NAMES = {}; const CH_ORDER = []; const CH_TARGETS = {}; let FOLDERS = [];
function applyProjectMeta() {
  const cm = DATA.chapters_meta || [];
  CH_ORDER.length = 0;
  cm.forEach(c => { CH_ORDER.push(c.id); CH_NAMES[c.id] = c.title; CH_TARGETS[c.id] = +c.target || 0; });
  FOLDERS = DATA.folders || [];
}

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  return r.json();
}

function setSaveState(cls, txt) { const el = $("#save-state"); el.className = cls; el.textContent = txt; }

/* ---------------- 极简 Markdown 渲染 ---------------- */
function md2html(md) {
  const lines = String(md || "").split("\n");
  let out = [], inList = false, inCode = false, buf = [];
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  const flushCode = () => { if (inCode) { out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>"); buf = []; inCode = false; } };
  const inline = t => {
    t = esc(t);
    t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(?<!\*)\*(?!\*)(.+?)\*/g, "<i>$1</i>");
    t = t.replace(/`(.+?)`/g, "<code>$1</code>");
    return t;
  };
  for (const ln of lines) {
    if (ln.trim().startsWith("```")) { if (inCode) flushCode(); else { closeList(); inCode = true; } continue; }
    if (inCode) { buf.push(ln); continue; }
    const h = ln.match(/^(#{1,4})\s+(.*)/);
    if (h) { closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    if (/^\s*[-*]\s+/.test(ln)) { if (!inList) { out.push("<ul>"); inList = true; } out.push("<li>" + inline(ln.replace(/^\s*[-*]\s+/, "")) + "</li>"); continue; }
    closeList();
    if (/^\s*\|/.test(ln)) {
      const cells = ln.trim().replace(/^\||\|$/g, "").split("|").map(inline);
      if (cells.every(c => /^:?-{2,}:?$/.test(c.replace(/<[^>]+>/g, "").trim()))) continue;
      out.push("<table><tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr></table>");
      continue;
    }
    if (!ln.trim()) continue;
    out.push("<p>" + inline(ln) + "</p>");
  }
  closeList(); flushCode();
  return out.join("\n");
}

/* ---------------- 视图切换 ---------------- */
$("#nav").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  view = b.dataset.view;
  $$("#nav button").forEach(x => x.classList.toggle("active", x === b));
  render();
});

/* ---------------- 论文项目标签栏 ---------------- */
function renderPtabs() {
  const el = $("#ptabs");
  if (!el) return;
  const ps = DATA.projects || [];
  el.innerHTML = ps.map(t => `<div class="ptab ${t.id === DATA.pid ? "active" : ""}" onclick="switchProject('${t.id}')" title="${t.legacy ? "内置综述项目" : "创建于 " + (t.created || "?")}">${esc(t.name)}${(t.type && !t.name.includes(t.type)) ? `<span class="pt-type">${esc(t.type)}</span>` : ""}</div>`).join("")
    + `<div class="ptab add" onclick="newProject()">＋ 新建论文</div>`;
}

window.switchProject = async function (pid) {
  if (pid === DATA.pid) return;
  setSaveState("saving", "切换论文项目…");
  const r = await api("/api/project/switch", { pid });
  if (r && r.ok) {
    DATA = await api("/api/data");
    applyProjectMeta();
    if (!CH_ORDER.includes(curCh)) curCh = CH_ORDER[0] || "ch1";
    litFilter.chapter = ""; litFilter.folder = "";
    setSaveState("", "就绪");
    render();
  } else { setSaveState("", "就绪"); alert("切换失败：" + ((r && r.error) || "未知")); }
};

window.newProject = function () {
  const withPdf = DATA.literature.filter(x => x.pdf_path);
  modal("新建论文项目（对标一篇优秀论文来写）", `
    <label>论文名（你要写的论文题目/方向）</label><input id="np-name" placeholder="例如：钠离子电池层状氧化物正极综述">
    <label>论文类型</label><select id="np-type"><option>综述</option><option>研究论文</option><option>学位论文章节</option><option>其他</option></select>
    <label>对标论文 PDF（想对标学习的优秀论文）</label>
    <select id="np-src"><option value="">— 从当前文献库选择 —</option>${withPdf.map(x => `<option value='${esc(JSON.stringify(x.pdf_path)).replace(/'/g, "&#39;")}'>[${x.orig_num ?? "导"}] ${esc((x.title || "").slice(0, 42))}</option>`).join("")}</select>
    <label>或粘贴 PDF 完整路径（留空 = 仅建档，不AI拆解）</label><input id="np-pdf" placeholder="E:\\...\\xxx.pdf">
    <p class="small">提供对标 PDF 时，创建过程调用 DeepSeek 自动拆解（<b>约1-2分钟，请勿关闭页面</b>），生成：拆解报告 + 你的论文大纲草稿 + 章节结构 + 分类文件夹，并把对标论文登记为该项目文献第 1 号（不自动翻译）。</p>`,
    async () => {
      const name = $("#np-name").value.trim();
      if (!name) { alert("请填写论文名"); return; }
      let pdf = $("#np-pdf").value.trim();
      if (!pdf) { try { pdf = JSON.parse($("#np-src").value || '""'); } catch (e) { pdf = ""; } }
      $("#m-ok").textContent = "创建+AI拆解中…（最长约2分钟）";
      $("#m-ok").disabled = true;
      const r = await api("/api/project/create", { name, type: $("#np-type").value, benchmark_pdf: pdf, ai: true });
      if (r && r.ok) {
        closeModal();
        DATA = await api("/api/data");
        applyProjectMeta();
        curCh = CH_ORDER[0] || "ch1";
        render();
        alert(`项目「${name}」已创建并切换${r.decomposed
          ? `\n✅ AI拆解完成：拆解报告 ${r.analysis_chars} 字 · ${(r.chapters || []).length} 章 · ${(r.folders || []).length} 个分类文件夹`
          : `\n⚠️ AI拆解未完成（${r.error || "未提供对标PDF"}）\n已按默认5章结构建档，可稍后在「对标拆解」页点「重新AI拆解」`}`);
      } else {
        $("#m-ok").disabled = false;
        $("#m-ok").textContent = "确定";
        alert("创建失败：" + ((r && r.error) || "未知"));
      }
    });
};

window.reDecompose = async function () {
  if (!confirm("重新AI拆解对标论文？约1-2分钟，将覆盖拆解报告。")) return;
  const ow = confirm("是否同时用AI结果覆盖 章节结构/大纲/分类文件夹？\n（选「取消」= 只更新拆解报告）");
  setSaveState("saving", "AI拆解中…（约1-2分钟）");
  const r = await api("/api/project/decompose", { overwrite: ow });
  setSaveState("", "就绪");
  if (r && r.ok) {
    DATA = await api("/api/data"); applyProjectMeta(); render();
    alert("拆解完成：报告 " + r.analysis_chars + " 字" + (r.overwrite ? "，章节/大纲/文件夹已同步覆盖" : ""));
  } else alert("拆解失败：" + ((r && r.error) || "未知"));
};

window.deleteProject = async function () {
  if (DATA.legacy) { alert("内置综述项目不可删除"); return; }
  if (!confirm(`删除论文项目「${DATA.pname}」？\n（写作数据移入回收站可找回；文献PDF文件不受影响）`)) return;
  if (!confirm("再次确认删除？（可在 论文项目/_回收/ 找回）")) return;
  const r = await api("/api/project/delete", { pid: DATA.pid });
  if (r && r.ok) { DATA = await api("/api/data"); applyProjectMeta(); curCh = CH_ORDER[0] || "ch1"; render(); }
  else alert("删除失败：" + ((r && r.error) || "未知"));
};

function render() {
  renderPtabs();
  const m = $("#mcontent") || $("#main");
  if (view === "dash") return renderDash(m);
  if (view === "outline") return renderOutline(m);
  if (view === "write") return renderWrite(m);
  if (view === "lit") return renderLit(m);
  if (view === "fig") return renderFig(m);
  if (view === "translate") return renderTranslate(m);
  if (view === "analysis") {
    const bm = DATA.benchmark || {};
    m.innerHTML = `<h1>🔍 对标拆解 <span class="small">${esc(DATA.pname || "")}${bm.title ? " · 对标：" + esc(String(bm.title).slice(0, 44)) : ""}</span></h1>
    ${!DATA.legacy ? `<div class="row">
      ${bm.pdf_path ? `<button class="btn sec" onclick="reDecompose()">🔄 重新AI拆解</button><span class="small">对标PDF：${esc(bm.pdf_path)}</span>` : `<span class="small warn-note">未设置对标PDF——新建项目时未提供，无法AI拆解</span>`}
      <button class="btn warn mini" onclick="deleteProject()">🗑 删除本项目</button></div>` : ""}
    <div class="mdview md">${md2html(DATA.analysis_md || "暂无拆解报告")}</div>`;
    return;
  }
  if (view === "dl") {
    m.innerHTML = DATA.legacy
      ? `<h1>📥 文献下载台账</h1><p class="small">目录：${esc(DATA.lit_root)}</p><div class="mdview md">${md2html(DATA.dl_log_md || "暂无台账")}</div>`
      : `<h1>📥 文献下载台账</h1><div class="warn-note">论文项目「${esc(DATA.pname)}」使用自己的文献库目录：<b>${esc(DATA.lit_root)}</b><br>新文献用「批量导入」入库即可；下载台账仅内置综述项目维护。</div>`;
    return;
  }
  if (view === "export") return renderExport(m);
}

/* ---------------- 总览 ---------------- */
function renderDash(m) {
  const zh = DATA.chapters.filter(c => c.lang === "zh");
  const en = DATA.chapters.filter(c => c.lang === "en");
  const totalZh = zh.reduce((s, c) => s + c.words, 0);
  const tt = CH_ORDER.reduce((s, c) => s + (CH_TARGETS[c] || 0), 0) || 14000;
  const lit = DATA.literature;
  const withPdf = lit.filter(x => x.pdf_path).length;
  const verified = lit.filter(x => x.verified).length;
  const shared = lit.filter(x => (x.shared_with || []).length).length;
  const byFolder = {};
  lit.forEach(x => { byFolder[x.folder] = byFolder[x.folder] || { n: 0, pdf: 0 }; byFolder[x.folder].n++; if (x.pdf_path) byFolder[x.folder].pdf++; });
  const rows = Object.entries(byFolder).sort().map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${v.pdf}</td><td><div class="bar"><i style="width:${Math.round(v.pdf / v.n * 100)}%"></i></div></td></tr>`).join("");
  m.innerHTML = `
  <h1>📊 总览看板 <span class="small">— ${esc(DATA.pname || "")}（${esc(DATA.ptype || "")}）</span></h1>
  <div class="grid c4">
    <div class="card stat"><div class="num">${totalZh}</div><div class="lbl">中文字数（目标 ~${tt}）</div><div class="bar"><i style="width:${Math.min(100, Math.round(totalZh / tt * 100))}%"></i></div></div>
    <div class="card stat"><div class="num">${en.reduce((s, c) => s + c.words, 0)}</div><div class="lbl">英文词数</div></div>
    <div class="card stat"><div class="num">${lit.length}</div><div class="lbl">文献条目（含新增）</div></div>
    <div class="card stat"><div class="num">${withPdf}<span class="small">/${lit.length}</span></div><div class="lbl">已下载PDF / 核实通过 ${verified}</div></div>
  </div>
  <h2>各章进度</h2>
  <div class="card"><table><tr><th>章节</th><th>中文字数</th><th>目标</th><th>进度</th><th>英文词数</th></tr>
  ${CH_ORDER.map(id => {
    const c = zh.find(x => x.id === id), e2 = en.find(x => x.id === id);
    const target = CH_TARGETS[id] || 0;
    const w = c ? c.words : 0, pct = target ? Math.min(100, Math.round(w / target * 100)) : 0;
    return `<tr><td>${id} ${esc(CH_NAMES[id] || "")}</td><td>${w}</td><td>${target || "-"}</td><td><div class="bar"><i style="width:${pct}%"></i></div></td><td>${e2 ? e2.words : 0}</td></tr>`;
  }).join("")}</table></div>
  <h2>文献库按分类${shared ? ` <span class="tag share">🔗 ${shared} 篇与其他论文共享</span>` : ""}</h2>
  <div class="card"><table><tr><th>分类文件夹</th><th>条目</th><th>已下载</th><th>下载率</th></tr>${rows || "<tr><td colspan=4>暂无</td></tr>"}</table></div>
  <div class="warn-note">⚠️ 所有文献默认「待核实」。投稿前请逐条核对 DOI 与题录（见「使用说明」核实清单）。</div>`;
}

/* ---------------- 大纲 ---------------- */
function renderOutline(m) {
  const o = DATA.outline;
  m.innerHTML = `<h1>🗂 大纲工作台 <span class="small">— ${esc(DATA.pname || "")}</span>
  <button class="btn sec" onclick="editSection(null)">＋ 新增小节</button>
  <button class="btn sec" onclick="manageChapters()">⚙ 章节/文件夹管理</button></h1>
  <div class="card"><table><tr><th>章节</th><th>小节</th><th>字数目标</th><th>文献配额</th><th>状态</th><th>要点</th><th></th></tr>
  ${o.map((s, i) => `<tr>
    <td>${s.chapter}</td><td><b>${esc(s.title)}</b></td><td>${s.words}</td><td>${s.refs}</td>
    <td><span class="badge ${s.status === "初稿完成" ? "yes" : "no"}">${esc(s.status)}</span></td>
    <td class="small">${esc(s.points)}</td>
    <td><button class="btn sec mini" onclick="editSection(${i})">编辑</button> <button class="btn warn mini" onclick="delSection(${i})">删</button></td>
  </tr>`).join("")}</table></div>
  <div class="small">大纲即 03_大纲/outline.json，编辑后自动保存。</div>`;
}

window.editSection = function (i) {
  const s = i === null ? { chapter: CH_ORDER[0] || "ch1", title: "", words: 0, refs: 0, status: "未开始", points: "" } : DATA.outline[i];
  modal(`编辑小节`, `
    <label>所属章节</label><select id="f-ch">${CH_ORDER.map(c => `<option ${s.chapter === c ? "selected" : ""}>${c}</option>`).join("")}</select>
    <label>小节标题</label><input id="f-t" value="${esc(s.title)}">
    <div style="display:flex;gap:12px"><div style="flex:1"><label>字数目标</label><input id="f-w" type="number" value="${s.words}"></div>
    <div style="flex:1"><label>文献配额</label><input id="f-r" type="number" value="${s.refs}"></div></div>
    <label>状态</label><select id="f-s">${["未开始", "撰写中", "初稿完成", "已润色"].map(x => `<option ${s.status === x ? "selected" : ""}>${x}</option>`).join("")}</select>
    <label>内容要点</label><textarea id="f-p" rows="4">${esc(s.points)}</textarea>`,
    async () => {
      const obj = { chapter: $("#f-ch").value, title: $("#f-t").value, words: +$("#f-w").value, refs: +$("#f-r").value, status: $("#f-s").value, points: $("#f-p").value };
      if (i === null) DATA.outline.push(obj); else DATA.outline[i] = obj;
      await api("/api/outline", { outline: DATA.outline });
      closeModal(); render();
    });
};
window.delSection = async function (i) { DATA.outline.splice(i, 1); await api("/api/outline", { outline: DATA.outline }); render(); };

window.manageChapters = function () {
  const cm = DATA.chapters_meta || [];
  const legacy = !!DATA.legacy;
  modal("章节与分类文件夹" + (legacy ? "（内置项目：仅可改章标题/字数目标）" : ""), `
    <label>章节表${legacy ? "" : "（可增删；文件名 stem 用于成稿 md 文件）"}</label>
    <div id="mc-list">${cm.map(c => `<div class="row" style="margin-bottom:6px">
      <input class="mc-id" value="${esc(c.id)}" style="width:64px" ${legacy ? "disabled" : ""}>
      <input class="mc-stem" value="${esc(c.stem)}" style="width:150px" ${legacy ? "disabled" : ""} title="成稿文件名">
      <input class="mc-title" value="${esc(c.title)}" style="flex:1" title="章标题">
      <input class="mc-target" type="number" value="${c.target}" style="width:86px" title="字数目标">
      ${legacy ? "" : `<button class="btn warn mini" onclick="this.parentElement.remove()">删</button>`}
    </div>`).join("")}</div>
    ${legacy ? "" : `<button class="btn sec mini" onclick="mcAdd()">＋ 加一章</button>`}
    <label>分类文件夹${legacy ? "（内置综述项目固定，不可改）" : "（每行一个）"}</label>
    ${legacy ? `<textarea rows="3" disabled>${FOLDERS.join("\n")}</textarea>`
             : `<textarea id="mc-folders" rows="4">${FOLDERS.join("\n")}</textarea>`}`,
    async () => {
      const chapters = [];
      $$("#mc-list .row").forEach(r => {
        const id = $(".mc-id", r).value.trim(), title = $(".mc-title", r).value.trim();
        if (!id || !title) return;
        chapters.push({ id, stem: $(".mc-stem", r).value.trim() || id, title, target: +$(".mc-target", r).value || 0 });
      });
      if (!chapters.length) { alert("至少保留一章"); return; }
      const body = { pid: DATA.pid, chapters };
      if (!legacy) body.folders = $("#mc-folders").value.split("\n").map(s => s.trim()).filter(Boolean);
      const r = await api("/api/project/update", body);
      if (r && r.ok) {
        closeModal();
        DATA = await api("/api/data"); applyProjectMeta();
        if (!CH_ORDER.includes(curCh)) curCh = CH_ORDER[0];
        render();
      } else alert("保存失败：" + ((r && r.error) || "未知"));
    });
};
window.mcAdd = function () {
  const div = document.createElement("div");
  div.className = "row"; div.style.marginBottom = "6px";
  div.innerHTML = `<input class="mc-id" placeholder="chN" style="width:64px"><input class="mc-stem" placeholder="chN_短名" style="width:150px"><input class="mc-title" placeholder="章标题" style="flex:1"><input class="mc-target" type="number" value="1500" style="width:86px"><button class="btn warn mini" onclick="this.parentElement.remove()">删</button>`;
  $("#mc-list").appendChild(div);
};

/* ---------------- 写作 ---------------- */
async function renderWrite(m) {
  m.innerHTML = `
  <h1>✍️ 章节写作</h1>
  <div class="row">
    <select id="w-ch">${CH_ORDER.map(c => `<option value="${c}" ${c === curCh ? "selected" : ""}>${c} ${CH_NAMES[c]}</option>`).join("")}</select>
    <select id="w-lang"><option value="zh" ${curLang === "zh" ? "selected" : ""}>中文版</option><option value="en" ${curLang === "en" ? "selected" : ""}>English</option></select>
    <span id="w-count" class="small"></span>
    <button class="btn sec" id="w-save">立即保存</button>
    <label class="small"><input type="checkbox" id="w-preview"> 显示预览</label>
  </div>
  <div id="write-wrap">
    <div id="writer-col"><textarea id="editor" spellcheck="false" placeholder="在此撰写该章内容（Markdown）……"></textarea></div>
    <div id="ref-col">
      <div class="card"><h2>本章文献（${DATA.literature.filter(x => x.chapter === curCh).length}）</h2>
      <div style="max-height:280px;overflow-y:auto">${DATA.literature.filter(x => x.chapter === curCh).map(x =>
        `<div style="padding:5px 0;border-bottom:1px solid #f0f0f0"><span class="tag">${x.orig_num ?? "新"}</span> ${esc(x.authors)} (${x.year ?? "?"})<br><span class="small">${esc(x.title)}</span>
         ${x.pdf_path ? `<button class="btn mini" style="margin-left:6px" onclick="zoteroOpen('${esc(x.id)}')" title="在Zotero中打开并选中">Zotero</button>
         <button class="btn mini" style="margin-left:4px" onclick="openPdf(${esc(JSON.stringify(x.pdf_path))})">PDF</button>` : ""}</div>`).join("") || "无"}</div></div>
      <div class="card"><h2>原文拆解参考</h2><div class="small" style="max-height:260px;overflow-y:auto">${md2html((DATA.analysis_md || "").split("## 三、")[1] || "")}</div></div>
      <div class="card" id="pv-card" style="display:none"><h2>预览</h2><div id="preview" class="md"></div></div>
    </div>
  </div>`;
  const ed = $("#editor");
  const load = async () => {
    const r = await api(`/api/chapter?id=${curCh}&lang=${curLang}`);
    ed.value = r.text || "";
    updateCount();
  };
  const updateCount = () => {
    const t = ed.value;
    const cjk = (t.match(/[\u4e00-\u9fff]/g) || []).length;
    const en = (t.match(/[A-Za-z]+/g) || []).length;
    $("#w-count").textContent = `中文字符 ${cjk} · 英文词 ${en}`;
  };
  ed.addEventListener("input", () => {
    updateCount();
    setSaveState("saving", "输入中…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveCh, 1500);
  });
  async function saveCh() {
    setSaveState("saving", "保存中…");
    await api("/api/chapter", { id: curCh, lang: curLang, text: ed.value });
    setSaveState("saved", "已自动保存 ✓");
  }
  $("#w-save").onclick = saveCh;
  $("#w-ch").onchange = e => { curCh = e.target.value; renderWrite(m); };
  $("#w-lang").onchange = e => { curLang = e.target.value; renderWrite(m); };
  $("#w-preview").onchange = e => {
    $("#pv-card").style.display = e.target.checked ? "" : "none";
    if (e.target.checked) $("#preview").innerHTML = md2html(ed.value);
    else $("#editor").oninput = null;
    if (e.target.checked) ed.addEventListener("input", () => { $("#preview").innerHTML = md2html(ed.value); });
  };
  await load();
}
window.openPdf = async function (p) {
  const r = await api("/api/open", { path: p });
  if (!r || !r.ok) alert("打开失败：" + ((r && r.error) || "未知错误") + "\n路径: " + p);
};

/* ---------------- Zotero 集成 ---------------- */
window.zoteroOpen = async function (id) {
  setSaveState("saving", "Zotero处理中…");
  const r = await api("/api/zotero/open", { id });
  setSaveState("", "就绪");
  if (!r || !r.ok) { alert("Zotero打开失败：" + ((r && r.error) || "未知错误")); return; }
  const rec = DATA.literature.find(x => x.id === id);
  if (rec && r.key) { rec.zotero_key = r.key; render(); }
};

window.zoteroImportOne = async function (id) {
  const r = await api("/api/zotero/import", { id });
  if (!r || !r.ok) { alert("录入失败：" + ((r && r.error) || "未知错误")); return false; }
  const rec = DATA.literature.find(x => x.id === id);
  if (rec) rec.zotero_key = r.key;
  return true;
};

window.zoteroImportAll = async function () {
  const el = $("#zot-progress");
  const todo = DATA.literature.filter(x => !x.zotero_key);
  let ok = 0, fail = 0;
  for (let i = 0; i < todo.length; i++) {
    if (el) el.textContent = `录入中 ${i + 1}/${todo.length}（成功${ok} 失败${fail}）`;
    const r = await api("/api/zotero/import", { id: todo[i].id });
    if (r && r.ok) { ok++; todo[i].zotero_key = r.key; }
    else fail++;
  }
  render();
  const needAttach = DATA.literature.filter(x => x.pdf_path && x.zotero_key).length;
  let attachMsg = "";
  if (needAttach && confirm(`录入完成：成功 ${ok}，失败 ${fail}。\n\n检测到 ${needAttach} 篇PDF尚未挂载到Zotero条目。\n现在自动挂载吗？（将自动关闭并重新打开Zotero，约1分钟）`)) {
    if (el) el.textContent = "正在关闭Zotero…";
    await api("/api/zotero/close", {});
    if (el) el.textContent = "挂载PDF中…";
    const r = await api("/api/zotero/attach", {});
    if (r && r.ok) attachMsg = `挂载：新挂 ${r.attached} 篇，已有附件跳过 ${r.skipped_existing} 篇。`;
    else { alert("挂载失败：" + ((r && r.error) || "未知错误")); attachMsg = "挂载失败。"; }
    if (el) el.textContent = "重启Zotero…";
    await api("/api/zotero/launch", {});
  }
  if (el) el.textContent = `完成：录入成功 ${ok}/失败 ${fail}。${attachMsg}`;
  render();
};

window.openFolder = async function (p) {
  const r = await api("/api/open_folder", { path: p });
  if (!r || !r.ok) alert("打开文件夹失败：" + ((r && r.error) || "未知错误"));
};

window.zoteroScan = async function () {
  const el = $("#zot-progress");
  if (el) el.textContent = "扫描Zotero中…";
  const r = await api("/api/zotero/scan", {});
  if (el) el.textContent = "";
  if (!r || !r.ok) { alert("扫描失败：" + ((r && r.error) || "未知错误")); return; }
  let msg = `扫描完成：新拷贝文件 ${r.matched} 篇，新记录Zotero位置 ${r.keys} 条。`;
  if (r.unmatched_count) msg += `\n\nZotero中另有 ${r.unmatched_count} 条文献不属于本综述清单（含中文论文等），未处理。`;
  alert(msg);
  render();
};

window.zoteroAttach = async function () {
  const el = $("#zot-progress");
  if (el) el.textContent = "挂载中…";
  const r = await api("/api/zotero/attach", {});
  if (el) el.textContent = "";
  if (!r || !r.ok) { alert(r && r.error ? r.error : "挂载失败（未知错误）"); return; }
  alert(`挂载完成：新挂载 ${r.attached} 篇，Zotero已有附件跳过 ${r.skipped_existing} 篇。\n已自动备份原数据库。现在可重新打开 Zotero 查看附件。`);
  render();
};

window.openDoi = function (doi) {
  if (doi) window.open("https://doi.org/" + doi, "_blank");
};

/* ---------------- 文献库·原地阅读器（不跳转） ---------------- */
function transCell(x) {
  if (!x.pdf_path) return '<span class="small" title="无本地PDF，无法翻译">—</span>';
  if (x.zh_self) return '<span class="tag g" title="该条目本身就是中文翻译版">已是中文版</span>';
  if (x.zh_pdf) return `<button class="btn mini" title="原地打开阅读器直接读中文翻译" onclick="litRead('${esc(x.id)}','zh')">📖 读中文</button>`;
  if (x.tr_jid) return `<button class="btn mini" title="翻译任务进行中——点击原地阅读，完成后自动显示中文" onclick="litRead('${esc(x.id)}')">⏳ 读中文</button>`;
  return `<button class="btn sec mini" onclick="litTranslate('${esc(x.id)}')" title="DeepSeek全文翻译后原地阅读（约1-3分钟，期间可先读原文）">🌐 翻译</button>`;
}

window.litTranslate = async function (id) {
  const rec = DATA.literature.find(x => x.id === id);
  if (!rec || !rec.pdf_path) { alert("该条目没有本地PDF"); return; }
  setSaveState("saving", "提交翻译任务…");
  const r = await api("/api/tr/create", { path: rec.pdf_path, skip_refs: true });
  setSaveState("", "就绪");
  if (r && r.id) {
    rec.tr_jid = r.id;
    api("/api/literature", { literature: DATA.literature });
    litRead(id);
  } else {
    alert("创建翻译任务失败：" + ((r && r.error) || "未知（翻译服务可能未启动）"));
  }
};

let rd = null; // 阅读器状态: {rec, mode, zoom, srcPages, zhPages, zhSrc:{kind,path|jid}, job, timer}

window.litRead = async function (id, mode) {
  const rec = DATA.literature.find(x => x.id === id);
  if (!rec || !rec.pdf_path) { alert("该条目没有本地PDF"); return; }
  rdClose();
  let zhSrc = null;
  if (rec.zh_pdf) zhSrc = { kind: "file", path: rec.zh_pdf };
  else if (rec.tr_jid) zhSrc = { kind: "job", jid: rec.tr_jid };
  rd = { rec, mode: mode || (zhSrc && zhSrc.kind === "file" ? "zh" : "both"), zoom: 100, srcPages: 0, zhPages: 0, zhSrc, job: null, timer: null };
  try { const r = await api("/api/pdf_info?path=" + encodeURIComponent(rec.pdf_path)); rd.srcPages = r.pages || 0; } catch (e) { }
  await rdRefreshZh();
  rdCloseDom();
  const div = document.createElement("div");
  div.id = "lit-reader";
  document.body.appendChild(div);
  rdRender();
  if (zhSrc && zhSrc.kind === "job") rdPoll();
};

async function rdRefreshZh() {
  if (!rd || !rd.zhSrc) return;
  if (rd.zhSrc.kind === "file") {
    try { const r = await api("/api/pdf_info?path=" + encodeURIComponent(rd.zhSrc.path)); rd.zhPages = r.pages || 0; } catch (e) { rd.zhPages = 0; }
    return;
  }
  try {
    const j = await api("/api/tr/job", { jid: rd.zhSrc.jid });
    rd.job = j;
    rd.zhPages = j.status === "done" ? j.pages : 0;
    if (j.status === "done" && !rd.attached) {
      rd.attached = true;
      const a = await api("/api/tr/attach_zh", { id: rd.rec.id });
      if (a && a.ok) {
        rd.zhSrc = { kind: "file", path: a.path };
        rd.rec.zh_pdf = a.path;
        rd.rec.tr_done = true;
        try { const info = await api("/api/pdf_info?path=" + encodeURIComponent(a.path)); rd.zhPages = info.pages || 0; } catch (e) { }
      }
    }
  } catch (e) { }
}

function rdPoll() {
  rd.timer = setInterval(async () => {
    await rdRefreshZh();
    if (!document.getElementById("lit-reader")) { clearInterval(rd.timer); return; }
    if (rd.zhSrc.kind === "file") { clearInterval(rd.timer); rdRender(); return; }
    const j = rd.job || {};
    const b = document.getElementById("rd-banner");
    if (b) b.textContent = `翻译中 ${j.progress || 0}% · ${j.stage_label || j.status || ""}`;
    const bar = document.getElementById("rd-bar");
    if (bar) bar.style.width = (j.progress || 0) + "%";
  }, 4000);
}

function rdRender() {
  const el = document.getElementById("lit-reader");
  if (!el || !rd) return;
  const dpi = rd.zoom > 200 ? 220 : rd.zoom > 100 ? 150 : 96;
  const srcImg = (n) => `/api/pdf_page?path=${encodeURIComponent(rd.rec.pdf_path)}&n=${n}&dpi=${dpi}`;
  const zhImg = (n) => rd.zhSrc.kind === "file"
    ? `/api/pdf_page?path=${encodeURIComponent(rd.zhSrc.path)}&n=${n}&dpi=${dpi}`
    : `/api/tr/page?jid=${rd.zhSrc.jid}&n=${n}&src=out&dpi=${dpi}`;
  const pages = Math.max(rd.srcPages || 0, rd.zhPages || 0);
  const zhReady = !!rd.zhSrc && (rd.zhSrc.kind === "file" || (rd.job && rd.job.status === "done"));
  const cellW = rd.mode === "both" ? Math.max(24, rd.zoom / 2) : rd.zoom;
  const img = (url, alt) => `<img src="${url}" loading="lazy" decoding="async"
      style="width:100%;border:1px solid #ddd;border-radius:6px;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.08)"
      alt="${alt}" onerror="this.style.display='none';this.nextElementSibling.style.display='block'">`;
  const fb = (t) => `<div class="small" style="display:none;padding:40px 12px;border:1px dashed #ccc;border-radius:6px;text-align:center">${t}</div>`;
  const cap = (n) => `<div class="small" style="text-align:center;margin:12px 0 5px;color:#888">— 第 ${n} / ${pages} 页 —</div>`;
  let body = "";
  for (let n = 1; n <= pages; n++) {
    const L = rd.mode !== "zh" ? `<div style="flex:0 0 auto;width:${cellW}%;min-width:260px">${img(srcImg(n), "原文第" + n + "页")}${fb("原文渲染失败")}</div>` : "";
    const R = rd.mode === "src" ? "" : (zhReady
      ? `<div style="flex:0 0 auto;width:${cellW}%;min-width:260px">${img(zhImg(n), "译文第" + n + "页")}${fb("该页暂无译文")}</div>`
      : `<div class="small" style="flex:0 0 auto;width:${cellW}%;min-width:260px;padding:60px 12px;border:1px dashed #ccc;border-radius:6px;text-align:center">🕐 翻译中…<br>完成后此处自动显示中文<br>可先切「📄 原文」阅读</div>`);
    body += `${cap(n)}<div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:nowrap">${L}${R}</div>`;
  }
  const mb = (m, label) => `<button class="btn ${rd.mode === m ? "" : "sec"} mini" onclick="rdMode('${m}')">${label}</button>`;
  const j = rd.job;
  const showBar = rd.zhSrc && rd.zhSrc.kind === "job" && !(j && j.status === "done");
  el.innerHTML = `
  <div class="rd-head"><div class="row" style="margin:0;flex-wrap:wrap">
    <b>📖 ${esc((rd.rec.title || "").slice(0, 46))}</b>
    <span class="small">${esc((rd.rec.authors || "").slice(0, 30))} (${rd.rec.year ?? "?"})</span>
    <span style="flex:1"></span>
    ${mb("src", "📄 原文")}${mb("zh", "🇨🇳 译文")}${mb("both", "📋 对照")}
    <button class="btn sec mini" onclick="rdZoom(-25)">➖</button>
    <span class="small" style="min-width:44px;text-align:center">${rd.zoom}%</span>
    <button class="btn sec mini" onclick="rdZoom(25)">➕</button>
    <button class="btn sec mini" onclick="rdFit()">适应</button>
    ${showBar ? `<span class="small"><span id="rd-banner">翻译中 ${j ? (j.progress || 0) : 0}%</span><div class="bar" style="width:140px"><i id="rd-bar" style="width:${j ? (j.progress || 0) : 0}%"></i></div></span>` : ""}
    <button class="btn warn mini" onclick="rdClose()">✕ 关闭</button>
  </div></div>
  <div class="rd-body">${body}</div>`;
}

window.rdMode = function (m) { if (rd) { rd.mode = m; rdRender(); } };
window.rdZoom = function (d) { if (rd) { rd.zoom = Math.max(50, Math.min(400, rd.zoom + d)); rdRender(); } };
window.rdFit = function () { if (rd) { rd.zoom = 100; rdRender(); } };
function rdCloseDom() { const el = document.getElementById("lit-reader"); if (el) el.remove(); }
window.rdClose = function () { if (rd && rd.timer) clearInterval(rd.timer); rd = null; rdCloseDom(); };

// 行内按钮组（Zotero主按钮 + PDF/文件夹/DOI）
function litBtns(x) {  if (!x.pdf_path) {
    return `<button class="btn mini" title="录入Zotero（仅题录）" onclick="zoteroOpen('${esc(x.id)}')">录Zotero</button>`;
  }
  return `<button class="btn mini" title="在Zotero阅读器中直接打开PDF（无附件时定位条目）" onclick="zoteroOpen('${esc(x.id)}')">打开</button>
  <button class="btn sec mini" title="系统默认程序打开PDF" onclick="openPdf(${esc(JSON.stringify(x.pdf_path))})">PDF</button>
  <button class="btn sec mini" title="资源管理器定位文件" onclick="openFolder(${esc(JSON.stringify(x.pdf_path))})">📁</button>
  ${(x.doi || (x.doi_cf || {}).doi) ? `<button class="btn sec mini" title="打开DOI页面" onclick="openDoi('${esc(x.doi || (x.doi_cf || {}).doi)}')">DOI</button>` : ""}
  ${x.zotero_key ? `<span class="tag g" title="Zotero key: ${esc(x.zotero_key)}">已录入</span>` : ""}
  ${(x.shared_with || []).length ? `<span class="tag share" title="同一文献同时收录于：${esc(x.shared_with.join("、"))}">🔗 ${esc(x.shared_with.join("、"))}</span>` : ""}`;
}

/* ---------------- 文献库 ---------------- */
function renderLit(m) {
  let list = DATA.literature.slice();
  if (litFilter.chapter) list = list.filter(x => x.chapter === litFilter.chapter);
  if (litFilter.folder) list = list.filter(x => x.folder === litFilter.folder);
  if (litFilter.onlyNoPdf) list = list.filter(x => !x.pdf_path);
  if (litFilter.onlyShared) list = list.filter(x => (x.shared_with || []).length);
  if (litFilter.q) { const q = litFilter.q.toLowerCase(); list = list.filter(x => (x.title + x.authors + x.journal + (x.raw || "")).toLowerCase().includes(q)); }
  list.sort((a, b) => (a.orig_num ?? 999) - (b.orig_num ?? 999));
  m.innerHTML = `
  <h1>📦 文献库（${DATA.literature.length} 条）</h1>
  <div class="row">
    <input id="l-q" placeholder="搜索题目/作者/期刊…" value="${esc(litFilter.q)}" style="width:260px">
    <select id="l-ch"><option value="">全部章节</option>${CH_ORDER.map(c => `<option value="${c}" ${litFilter.chapter === c ? "selected" : ""}>${c} ${CH_NAMES[c]}</option>`).join("")}</select>
    <select id="l-fd"><option value="">全部分类</option>${FOLDERS.map(f => `<option ${litFilter.folder === f ? "selected" : ""}>${f}</option>`).join("")}</select>
    <label class="small"><input type="checkbox" id="l-nopdf" ${litFilter.onlyNoPdf ? "checked" : ""}> 仅看未下载</label>
    ${(DATA.projects || []).length > 1 ? `<label class="small"><input type="checkbox" id="l-shared" ${litFilter.onlyShared ? "checked" : ""}> 🔗 只看跨论文共享</label>` : ""}
    <button class="btn" onclick="zoteroImportAll()">📚 一键录入Zotero</button>
    <button class="btn" onclick="batchImport()">📥 批量导入</button>
    <button class="btn sec" onclick="zoteroScan()" title="扫描Zotero里的PDF文件，匹配后拷入平台分类文件夹">📥 扫描Zotero回录</button>
    <button class="btn sec" onclick="zoteroAttach()" title="把平台PDF挂载为Zotero附件（需先完全退出Zotero）">📎 挂载PDF到Zotero</button>
    <span id="zot-progress" class="small"></span>
    <span class="small">Zotero: <b id="zot-st" style="color:#999">检测中…</b></span>
    <button class="btn" onclick="editLit(null)">＋ 手动添加</button>
    <span class="small">共 ${list.length} 条</span>
  </div>
  <div class="card" style="padding:0"><div style="max-height:calc(100vh - 220px);overflow:auto"><table>
  <tr><th>#</th><th>题录</th><th>章节</th><th>分类</th><th>核实</th><th>PDF</th><th>翻译</th><th></th></tr>
  ${list.map(x => {
    const i = DATA.literature.indexOf(x);
    return `<tr>
    <td>${x.orig_num ?? "新"}</td>
    <td style="max-width:440px"><b>${esc(x.authors)}</b> <span class="small">(${x.year ?? "?"})</span><br><span class="small">${esc(x.title)}</span><br><span class="small" style="color:#667">${esc(x.journal || "")}${x.doi ? " · DOI:" + esc(x.doi) : ""}</span></td>
    <td><span class="tag">${x.chapter}</span></td><td class="small">${esc(x.folder)}</td>
    <td><span class="badge ${x.verified ? "yes" : "no"}" style="cursor:pointer" onclick="toggleVerify(${i})">${x.verified ? "已核实" : "待核实"}</span></td>
    <td>${litBtns(x)}</td>
    <td>${transCell(x)}</td>
    <td><button class="btn sec mini" onclick="zoteroImportOne('${esc(x.id)}')" title="录入到Zotero">＋录入</button>
    <button class="btn sec mini" onclick="editLit(${i})">编辑</button></td></tr>`;
  }).join("")}</table></div></div>`;
  $("#l-q").oninput = e => { litFilter.q = e.target.value; clearTimeout(window._lt); window._lt = setTimeout(render, 300); };
  $("#l-ch").onchange = e => { litFilter.chapter = e.target.value; render(); };
  $("#l-fd").onchange = e => { litFilter.folder = e.target.value; render(); };
  $("#l-nopdf").onchange = e => { litFilter.onlyNoPdf = e.target.checked; render(); };
  const lsh = $("#l-shared");
  if (lsh) lsh.onchange = e => { litFilter.onlyShared = e.target.checked; render(); };
  // Zotero 状态检测
  api("/api/zotero/ping", {}).then(r => {
    const el = $("#zot-st");
    if (el) el.textContent = r && r.ok ? `运行中 (${r.version})` : "未运行";
    if (el) el.style.color = r && r.ok ? "#16864f" : "#b3181f";
  });
}
window.toggleVerify = async function (i) { DATA.literature[i].verified = !DATA.literature[i].verified; await api("/api/literature", { literature: DATA.literature }); render(); };
window.editLit = function (i) {
  const x = i === null ? { authors: "", title: "", journal: "", year: new Date().getFullYear(), doi: "", chapter: CH_ORDER[0] || "ch1", folder: FOLDERS[0] || "00_待分类", raw: "", source: "手动添加", verified: false, pdf_path: "", note: "" } : DATA.literature[i];
  modal(i === null ? "添加文献" : "编辑文献", `
    <label>作者</label><input id="f-a" value="${esc(x.authors)}">
    <label>题目</label><input id="f-t" value="${esc(x.title)}">
    <div style="display:flex;gap:12px"><div style="flex:2"><label>期刊</label><input id="f-j" value="${esc(x.journal || "")}"></div>
    <div style="flex:1"><label>年份</label><input id="f-y" type="number" value="${x.year ?? ""}"></div></div>
    <label>DOI</label><input id="f-d" value="${esc(x.doi || "")}">
    <div style="display:flex;gap:12px"><div style="flex:1"><label>章节标签</label><select id="f-c">${CH_ORDER.map(c => `<option ${x.chapter === c ? "selected" : ""}>${c}</option>`).join("")}</select></div>
    <div style="flex:1"><label>下载分类</label><select id="f-f">${FOLDERS.map(f => `<option ${x.folder === f ? "selected" : ""}>${f}</option>`).join("")}</select></div></div>
    <label>本地PDF路径</label><input id="f-p" value="${esc(x.pdf_path || "")}">
    <label>备注</label><textarea id="f-n" rows="2">${esc(x.note || "")}</textarea>`,
    async () => {
      const obj = { ...x, authors: $("#f-a").value, title: $("#f-t").value, journal: $("#f-j").value, year: +$("#f-y").value || null, doi: $("#f-d").value, chapter: $("#f-c").value, folder: $("#f-f").value, pdf_path: $("#f-p").value, note: $("#f-n").value };
      if (i === null) { obj.id = "man" + Date.now(); obj.orig_num = null; DATA.literature.push(obj); } else DATA.literature[i] = obj;
      await api("/api/literature", { literature: DATA.literature });
      closeModal(); render();
    });
};

/* ---------------- 图表规划 ---------------- */
function renderFig(m) {
  m.innerHTML = `<h1>🖼 图表规划 <button class="btn sec" onclick="editFig(null)">＋ 新增</button></h1>
  <div class="grid c2">${DATA.figures.map((f, i) => `
    <div class="card"><b>${esc(f.id)} · ${esc(f.title)}</b> <span class="tag">${esc(f.chapter)}</span>
    <p class="small" style="margin-top:8px">${esc(f.desc)}</p>
    <p class="small" style="margin-top:6px;color:#667">素材：${esc(f.source)}</p>
    <div style="margin-top:8px"><button class="btn sec mini" onclick="editFig(${i})">编辑</button> <button class="btn warn mini" onclick="delFig(${i})">删</button></div></div>`).join("") || "暂无规划"}</div>`;
}
window.editFig = function (i) {
  const f = i === null ? { id: "图1", title: "", chapter: CH_ORDER[0] || "ch1", desc: "", source: "" } : DATA.figures[i];
  modal(i === null ? "新增图表规划" : "编辑图表规划", `
    <div style="display:flex;gap:12px"><div style="flex:1"><label>编号</label><input id="f-id" value="${esc(f.id)}"></div>
    <div style="flex:1"><label>章节</label><select id="f-c">${CH_ORDER.map(c => `<option ${f.chapter === c ? "selected" : ""}>${c}</option>`).join("")}</select></div></div>
    <label>主题</label><input id="f-t" value="${esc(f.title)}">
    <label>内容要素</label><textarea id="f-d" rows="4">${esc(f.desc)}</textarea>
    <label>素材来源建议</label><input id="f-s" value="${esc(f.source)}">`,
    async () => {
      const obj = { id: $("#f-id").value, title: $("#f-t").value, chapter: $("#f-c").value, desc: $("#f-d").value, source: $("#f-s").value };
      if (i === null) DATA.figures.push(obj); else DATA.figures[i] = obj;
      await api("/api/figures", { figures: DATA.figures });
      closeModal(); render();
    });
};
window.delFig = async function (i) { DATA.figures.splice(i, 1); await api("/api/figures", { figures: DATA.figures }); render(); };

/* ---------------- 论文翻译（对接 digital/translator） ---------------- */
let trTimer = null;
let trPreview = { jid: null, mode: "both", zoom: 100 };

async function renderTranslate(m) {
  const h = await api("/api/tr/health", {}).catch(() => null);
  const ok = h && h.ok;
  const withPdf = DATA.literature.filter(x => x.pdf_path);
  m.innerHTML = `<h1>🌐 论文翻译 <span class="small">英文PDF → 中文（版式保真，DeepSeek）</span></h1>
  <div class="${ok ? "ok-note" : "warn-note"}">${ok
    ? `翻译服务运行中 · 模型 ${esc(h.model || "deepseek-chat")} · API密钥已配置`
    : "翻译服务未运行（点击任意操作会自动尝试拉起）"}</div>
  <div class="card"><h2>新建翻译任务</h2>
    <div class="row">
      <select id="tr-src" style="max-width:460px">
        <option value="">— 从文献库选择PDF —</option>
        ${withPdf.map(x => `<option value='${esc(JSON.stringify(x.pdf_path)).replace(/'/g, "&#39;")}'>[${x.orig_num ?? "导"}] ${esc((x.title || "").slice(0, 55))}</option>`).join("")}
      </select>
      <input id="tr-path" placeholder="或输入任意PDF完整路径" style="flex:1;min-width:240px">
    </div>
    <div class="row">
      <label class="small"><input type="checkbox" id="tr-skipref" checked> 跳过参考文献（更省更快）</label>
      <button class="btn" id="tr-go">开始翻译</button>
      <span id="tr-new-res" class="small"></span>
    </div>
    <p class="small">提示：一篇 15 页论文约需 1–3 分钟；费用走你自己配置的 DeepSeek key（翻译几篇约几分钱）。翻译完成后可回存到文献库分类文件夹。</p>
  </div>
  <div class="card"><h2>任务列表 <button class="btn sec mini" id="tr-refresh">刷新</button></h2>
    <div id="tr-jobs">加载中…</div>
  </div>
  <div id="tr-preview-box"></div>`;
  $("#tr-go").onclick = async () => {
    const path = $("#tr-path").value.trim() || JSON.parse($("#tr-src").value || '""');
    if (!path) { alert("请先选择文献或在输入框填写PDF路径"); return; }
    $("#tr-new-res").textContent = "提交中…";
    const r = await api("/api/tr/create", { path, skip_refs: $("#tr-skipref").checked });
    $("#tr-new-res").textContent = r && r.id ? `任务已创建：${r.id}（${r.pages}页）` : "失败：" + ((r && r.error) || "未知");
    loadTrJobs();
  };
  $("#tr-refresh").onclick = loadTrJobs;
  await loadTrJobs();
  clearInterval(trTimer);
  trTimer = setInterval(loadTrJobs, 4000);
}

async function loadTrJobs() {
  const el = $("#tr-jobs");
  if (!el) { clearInterval(trTimer); return; }
  const jobs = await api("/api/tr/jobs", {}).catch(() => null);
  if (!Array.isArray(jobs)) { el.innerHTML = '<span class="small">服务不可用</span>'; return; }
  if (!jobs.length) { el.innerHTML = '<span class="small">暂无任务</span>'; return; }
  el.innerHTML = `<table><tr><th>文件</th><th>状态</th><th>进度</th><th></th></tr>${jobs.map(j => {
    const pct = j.progress || 0;
    return `<tr>
      <td style="max-width:340px">${esc((j.filename || "").slice(0, 46))}<br><span class="small">${j.pages}页 · ${new Date((j.created_at || 0) * 1000).toLocaleTimeString()}</span></td>
      <td>${esc(j.stage_label || j.status || "")}${j.message ? `<br><span class="small">${esc(String(j.message).slice(0, 40))}</span>` : ""}</td>
      <td style="min-width:110px"><div class="bar"><i style="width:${pct}%"></i></div><span class="small">${pct}%</span></td>
      <td>
        <button class="btn mini" onclick="trPrev('${j.id}')">预览</button>
        ${j.status === "done" ? `<a class="btn mini" style="text-decoration:none" href="/api/tr/file?jid=${j.id}&kind=out" download>下载</a>
        <button class="btn sec mini" onclick="trSaveback('${j.id}','${esc((j.filename || "").replace(/'/g, ""))}')">回存</button>` : ""}
        <button class="btn warn mini" onclick="trDel('${j.id}')">删</button>
      </td></tr>`;
  }).join("")}</table>`;
}

window.trPrev = async function (jid) {
  clearInterval(trTimer);
  trPreview.jid = jid;
  await drawTrPreview();
};

window.trMode = function (m) { trPreview.mode = m; drawTrPreview(); };
window.trZoom = function (d) { trPreview.zoom = Math.max(50, Math.min(400, trPreview.zoom + d)); drawTrPreview(); };
window.trZoomFit = function () { trPreview.zoom = 100; drawTrPreview(); };

window.drawTrPreview = async function () {
  const j = await api("/api/tr/job", { jid: trPreview.jid }).catch(() => null);
  const pages = j ? j.pages : 0;
  const done = !!j && j.status === "done";
  const box = $("#tr-preview-box");
  if (!box) return;
  const dpi = trPreview.zoom > 200 ? 220 : trPreview.zoom > 100 ? 150 : 96;
  const img = (src, n) => `<img src="/api/tr/page?jid=${encodeURIComponent(trPreview.jid)}&n=${n}&src=${src}&dpi=${dpi}" loading="lazy" decoding="async"
      style="width:100%;border:1px solid #ddd;border-radius:6px;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.08)"
      alt="${src === "source" ? "原文" : "译文"}第${n}页"
      onerror="this.style.display='none';this.nextElementSibling.style.display='block'">`;
  const fb = (txt) => `<div class="small" style="display:none;padding:40px 12px;border:1px dashed #ccc;border-radius:6px;text-align:center">${txt}</div>`;
  const cap = (n) => `<div class="small" style="text-align:center;margin:14px 0 6px;color:#888">— 第 ${n} / ${pages} 页 —</div>`;
  const cellW = trPreview.mode === "both" ? Math.max(24, trPreview.zoom / 2) : trPreview.zoom;
  let body = "";
  if (trPreview.mode === "out" && !done) {
    body = `<div class="small" style="padding:80px 20px;text-align:center">翻译完成前暂无译文 · 当前进度 ${j ? (j.progress || 0) : 0}%（${esc(j ? (j.stage_label || "") : "")}）<br>可先切到「原文」或「中英对照」查看英文原文</div>`;
  } else {
    for (let n = 1; n <= pages; n++) {
      const l = `<div style="flex:0 0 auto;width:${cellW}%;min-width:260px">${img("source", n)}${fb("原文渲染失败，请确认翻译服务仍在运行")}</div>`;
      const r = done
        ? `<div style="flex:0 0 auto;width:${cellW}%;min-width:260px">${img("out", n)}${fb("该页暂无译文（可能整页均为被跳过的参考文献/图注）")}</div>`
        : `<div class="small" style="flex:0 0 auto;width:${cellW}%;min-width:260px;padding:60px 12px;border:1px dashed #ccc;border-radius:6px;text-align:center">翻译中 · ${j ? (j.progress || 0) : 0}%<br>完成后重开预览查看</div>`;
      body += trPreview.mode === "source"
        ? `${cap(n)}<div>${l}</div>`
        : trPreview.mode === "out"
          ? `${cap(n)}<div>${r}</div>`
          : `${cap(n)}<div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:nowrap">${l}${r}</div>`;
    }
  }
  const mb = (m, label) => `<button class="btn ${trPreview.mode === m ? "" : "sec"} mini" onclick="trMode('${m}')">${label}</button>`;
  box.innerHTML = `<div class="card"><h2>预览 ${esc(j ? (j.filename || "").slice(0, 40) : "")}</h2>
    <div class="row" style="flex-wrap:wrap;align-items:center">
      ${mb("source", "📄 原文")}${mb("out", "🇨🇳 译文")}${mb("both", "📋 中英对照")}
      <span style="width:14px"></span>
      <button class="btn sec mini" onclick="trZoom(-25)">➖ 缩小</button>
      <span class="small" style="min-width:48px;text-align:center">${trPreview.zoom}%</span>
      <button class="btn sec mini" onclick="trZoom(25)">➕ 放大</button>
      <button class="btn sec mini" onclick="trZoomFit()">适应宽度</button>
      <span class="small">下滑连续查看（共 ${pages} 页）</span>
      <button class="btn sec mini" onclick="trPrevClose()">关闭预览</button>
    </div>
    <div style="overflow:auto;max-height:76vh;margin-top:8px;padding:6px 10px;border:1px solid #e8e8e8;border-radius:8px;background:#fbfbfb">
      ${body}
    </div>
  </div>`;
  box.scrollIntoView({ behavior: "smooth" });
};

window.trPrevClose = function () { $("#tr-preview-box").innerHTML = ""; clearInterval(trTimer); trTimer = setInterval(loadTrJobs, 4000); };
window.trDel = async function (jid) { if (confirm("删除该翻译任务？")) { await api("/api/tr/delete", { jid }); loadTrJobs(); } };

window.trSaveback = async function (jid, fname) {
  const folders = FOLDERS;
  const def = "00_待分类";
  const rec = DATA.literature.find(x => (x.pdf_path || "").includes(fname.replace(/_中文版\.pdf$/, "")));
  modal("回存译文到文献库", `
    <label>保存到分类文件夹</label>
    <select id="sb-folder">${folders.map(f => `<option ${f === def ? "selected" : ""}>${f}</option>`).join("")}</select>
    <label>题名（可改）</label><input id="sb-title" value="${esc((rec && rec.title) || fname.replace(/\.pdf$/i, ""))}">
    <div style="display:flex;gap:12px"><div style="flex:1"><label>作者</label><input id="sb-authors" value="${esc((rec && rec.authors) || "")}"></div>
    <div style="flex:1"><label>期刊</label><input id="sb-journal" value="${esc((rec && rec.journal) || "")}"></div>
    <div style="flex:1"><label>年份</label><input id="sb-year" type="number" value="${(rec && rec.year) || ""}"></div></div>
    <label>DOI</label><input id="sb-doi" value="${esc((rec && (rec.doi || (rec.doi_cf || {}).doi)) || "")}">`,
    async () => {
      const r = await api("/api/tr/saveback", {
        jid, folder: $("#sb-folder").value,
        title: $("#sb-title").value, authors: $("#sb-authors").value,
        journal: $("#sb-journal").value, year: +$("#sb-year").value || null,
        doi: $("#sb-doi").value,
        orig_src: rec ? "文献库" : "手动路径",
      });
      closeModal();
      if (r && r.ok) { alert("已回存：" + r.path); DATA = await api("/api/data"); render(); }
      else alert("回存失败：" + ((r && r.error) || "未知"));
    });
};

/* ---------------- 批量导入 ---------------- */
window.batchImport = function () {
  modal("批量导入文献", `
    <label>方式一：扫描文件夹中的 PDF（自动提取题录并建议分类）</label>
    <div class="row"><input id="bi-root" value="${esc(DATA.lit_root)}" style="flex:1">
      <button class="btn" id="bi-scan">扫描</button></div>
    <p class="small">自动跳过已在库/已有_中文版的文件；DOI 命中 Crossref 时自动补全题录。</p>
    <label>方式二：粘贴 RIS / BibTeX（Zotero 导出）</label>
    <textarea id="bi-ris" rows="4" placeholder='TY  - JOUR&#10;TI  - ...&#10;AU  - ...&#10;DO  - 10.xxxx/...'></textarea>
    <div class="row"><button class="btn sec" id="bi-parse">解析</button></div>
    <div id="bi-result"></div>`,
    null, true);
  $("#bi-scan").onclick = async () => {
    $("#bi-result").innerHTML = '<span class="small">扫描与元数据提取中…（每篇约1秒）</span>';
    const r = await api("/api/import/scan", { root: $("#bi-root").value.trim() });
    if (!r.ok) { $("#bi-result").innerHTML = "失败: " + esc(r.error || ""); return; }
    renderImportItems(r.items, "PDF文件");
  };
  $("#bi-parse").onclick = async () => {
    const r = await api("/api/import/ris", { text: $("#bi-ris").value });
    if (!r.ok) { $("#bi-result").innerHTML = "失败: " + esc(r.error || ""); return; }
    renderImportItems(r.items, "题录");
  };
};

function renderImportItems(items, tag) {
  const box = $("#bi-result");
  if (!items.length) { box.innerHTML = '<span class="small">未发现可导入条目</span>'; return; }
  box.innerHTML = `<p class="small">发现 ${items.length} 条（${tag}）。逐条确认分类后提交：</p>
    <div style="max-height:300px;overflow:auto"><table>
    <tr><th>导</th><th>题录</th><th>建议分类</th></tr>
    ${items.map((it, i) => `<tr>
      <td><input type="checkbox" class="bi-ck" data-i="${i}" checked></td>
      <td style="max-width:330px"><b>${esc(it.title || "(无题名)")}</b><br>
        <span class="small">${esc(it.authors || "?")} · ${it.year || "?"} · ${esc(it.journal || "")}${it.doi ? " · " + esc(it.doi) : ""}</span></td>
      <td><select class="bi-folder" data-i="${i}">${FOLDERS.map(f => `<option ${f === it.suggest ? "selected" : ""}>${f}</option>`).join("")}</select></td>
    </tr>`).join("")}</table></div>
    <div class="row" style="margin-top:10px"><button class="btn" id="bi-commit">导入所选</button><span id="bi-commit-res" class="small"></span></div>`;
  $("#bi-commit").onclick = async () => {
    const sel = [];
    $$(".bi-ck").forEach(ck => {
      if (ck.checked) {
        const i = +ck.dataset.i;
        sel.push({ ...items[i], folder: $(`.bi-folder[data-i="${i}"]`).value });
      }
    });
    if (!sel.length) { alert("未勾选任何条目"); return; }
    $("#bi-commit-res").textContent = "导入中…";
    const r = await api("/api/import/commit", { items: sel });
    $("#bi-commit-res").textContent = r.ok ? `已导入 ${r.added} 条（库共 ${r.total} 条）` : "失败";
    if (r.ok) { DATA = await api("/api/data"); }
  };
}

/* ---------------- 翻译/导入结束 ---------------- */
async function renderExport(m) {
  m.innerHTML = `<h1>📤 导出 <span class="small">— ${esc(DATA.pname || "")}</span></h1>
  <div class="card">
    <h2>整篇导出</h2>
    <div class="row"><button class="btn" onclick="doExport('zh')">导出中文版（MD/HTML/Word）</button>
    <button class="btn" onclick="doExport('en')">导出英文版</button></div>
    <div id="exp-result"></div>
  </div>
  <div class="card"><h2>导出说明</h2>
  <p class="small">· MD：纯文本 Markdown，任何编辑器可开。<br>· HTML：双击浏览器打开，已排版。<br>· Word：优先直接生成 .docx；若本机无 python-docx，用 Word 打开 .html 后「另存为 .docx」。<br>· 导出位置：当前论文项目（${esc(DATA.pname || "")}）自己的导出文件夹，文件名以项目名命名。</p></div>`;
}
window.doExport = async function (lang) {
  $("#exp-result").innerHTML = "<p class='small'>导出中…</p>";
  const r = await api("/api/export", { lang });
  $("#exp-result").innerHTML = `<div class="ok-note">完成：中文 ${r.words_zh} 字 / 英文 ${r.words_en} 词<br>
  MD：<code>${esc(r.md)}</code><br>HTML：<code>${esc(r.html)}</code><br>Word：<code>${esc(String(r.docx))}</code></div>`;
};

/* ---------------- 弹窗 ---------------- */
function modal(title, bodyHtml, onOk, hideOk) {
  $("#modal").innerHTML = `<h3>${esc(title)}</h3>${bodyHtml}
  <div class="btnrow"><button class="btn sec" onclick="closeModal()">关闭</button>${hideOk ? "" : '<button class="btn" id="m-ok">确定</button>'}</div>`;
  $("#modal-bg").style.display = "flex";
  if (!hideOk) $("#m-ok").onclick = onOk;
}
window.closeModal = () => { $("#modal-bg").style.display = "none"; };

/* ---------------- 启动 ---------------- */
(async function init() {
  DATA = await api("/api/data");
  applyProjectMeta();
  if (!CH_ORDER.includes(curCh)) curCh = CH_ORDER[0] || "ch1";
  render();
})();
