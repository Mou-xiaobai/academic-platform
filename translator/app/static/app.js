/* ================= 版式保真翻译 · 前端 ================= */
var $ = function (s, r) { return (r || document).querySelector(s); };
var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

var state = {
  file: null, job: null, blocks: null, jobId: null,
  poll: null, lbIdx: 0, lbPages: 0, filter: '', dirty: new Set(), ver: Date.now()
};

var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

function fmtSize(n) {
  if (!n) return '—';
  var u = ['B', 'KB', 'MB', 'GB'], i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
}
function fmtDate(t) {
  if (!t) return '—';
  var d = new Date(t * 1000);
  function p(x) { return String(x).padStart(2, '0'); }
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function api(path, opts) {
  return fetch(path, opts || {}).then(function (res) {
    if (!res.ok) {
      return res.text().then(function (txt) {
        var msg = res.status + ' ' + res.statusText;
        try { msg = JSON.parse(txt).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
      });
    }
    return res.json();
  });
}

var toastTimer = null;
function toast(msg, ms) {
  var el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove('show'); }, ms || 2600);
}

/* ------------------------------ 路由 ------------------------------ */
var VIEWS = ['new', 'job', 'history', 'settings', 'about'];
function show(view) {
  if (VIEWS.indexOf(view) < 0) view = 'new';
  $$('.view').forEach(function (v) { v.classList.toggle('on', v.id === 'view-' + view); });
  var active = (view === 'job') ? 'new' : view;
  $$('.nav a').forEach(function (a) { a.classList.toggle('on', a.dataset.nav === active); });
  window.scrollTo(0, 0);
}

function route() {
  var h = location.hash.replace(/^#\/?/, '') || 'new';
  var parts = h.split('/');
  var view = parts[0];
  closeReader();
  if (view === 'job') {
    show('job');
    if (parts[1] && parts[1] !== state.jobId) openJob(parts[1]);
    return;
  }
  stopPoll();
  if (view === 'history') { show('history'); loadHistory(); }
  else if (view === 'settings') { show('settings'); loadSettings(); }
  else if (view === 'about') { show('about'); }
  else { show('new'); refreshFacts(); }
}

/* ------------------------------ 新建 ------------------------------ */
function setupUpload() {
  var drop = $('#drop'), input = $('#file'), chip = $('#chip');

  function pickFile(f) {
    if (!/\.pdf$/i.test(f.name)) { toast('只支持 PDF 文件'); return; }
    if (f.size > 90 * 1024 * 1024) { toast('文件超过 90MB'); return; }
    state.file = f;
    $('#fileName').textContent = f.name;
    $('#fileMeta').textContent = fmtSize(f.size) + ' · 待开始';
    chip.classList.add('show');
    $('#go').disabled = false;
    $('#goHint').textContent = '准备就绪';
  }
  function clearFile() {
    state.file = null; input.value = '';
    chip.classList.remove('show');
    $('#go').disabled = true;
    $('#goHint').textContent = '未选择文件';
  }

  drop.addEventListener('click', function () { input.click(); });
  drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
  drop.addEventListener('drop', function (e) {
    e.preventDefault(); drop.classList.remove('over');
    if (e.dataTransfer.files && e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', function () { if (input.files[0]) pickFile(input.files[0]); });
  $('#fileClear').addEventListener('click', function (e) { e.stopPropagation(); clearFile(); });
}

function submitJob() {
  if (!state.file) return;
  var go = $('#go');
  go.disabled = true;
  go.innerHTML = '<span class="spin"></span>上传中…';
  var fd = new FormData();
  fd.append('file', state.file);
  fd.append('tgt_lang', $('#optLang').value);
  fd.append('glossary', $('#optGlossary').value);
  fd.append('skip_references', $('#optRefs').checked ? '1' : '0');

  api('/api/jobs', { method: 'POST', body: fd }).then(function (job) {
    go.innerHTML = '开始翻译';
    go.disabled = false;
    state.file = null;
    $('#chip').classList.remove('show');
    $('#goHint').textContent = '未选择文件';
    location.hash = '#/job/' + job.id;
  }).catch(function (e) {
    go.disabled = false;
    go.innerHTML = '开始翻译';
    toast('上传失败：' + e.message, 4000);
  });
}

function refreshFacts() {
  api('/api/jobs').then(function (jobs) {
    $('#factJobs').textContent = jobs.length;
    $('#factPages').textContent = jobs.reduce(function (a, j) { return a + (j.pages || 0); }, 0);
  }).catch(function () { /* ignore */ });
}

/* ------------------------------ 任务视图 ------------------------------ */
var STEPS = [['parse', '解析'], ['segment', '切分'], ['translate', '翻译'], ['rebuild', '排版'], ['qa', '质检']];
var STATUS_TEXT = { queued: '排队', running: '进行中', rebuilding: '重排中', done: '完成', error: '出错' };

function stopPoll() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }

function openJob(id) {
  stopPoll();
  state.jobId = id; state.blocks = null; state.dirty.clear();
  $('#jobBody').innerHTML = '<div class="card"><span class="spin"></span> 载入任务…</div>';
  api('/api/jobs/' + id).then(function (job) {
    state.job = job;
    renderJob(job);
    if (job.status !== 'done' && job.status !== 'error') startPoll(id);
  }).catch(function () {
    $('#jobBody').innerHTML = '<div class="card">任务不存在或已删除。<a href="#/history">返回记录</a></div>';
  });
}

function stepperHtml(job) {
  var done = job.status === 'done';
  var idx = -1;
  STEPS.forEach(function (s, i) { if (s[0] === job.stage) idx = i; });
  return STEPS.map(function (s, i) {
    var cls = 'step';
    if (done || (idx > i)) cls += ' done';
    else if (idx === i) cls += ' act';
    return '<div class="' + cls + '"><span class="pt"></span>' + s[1] + '</div>';
  }).join('');
}

function renderJob(job) {
  var done = job.status === 'done';
  var err = job.status === 'error';
  var dl = done ? '<a class="btn primary" href="/api/jobs/' + job.id + '/download">下载中文 PDF</a>' : '';
  var again = done ? '<a class="btn" href="#/new">再译一篇</a>' : '';
  var read = done
    ? '<button class="btn" id="jobRead">在线阅读</button><button class="btn" id="jobReadCmp">中英对比</button>'
    : '<button class="btn" id="jobReadSrc">阅读原文</button>';
  var body = [
    '<div class="card">',
    '  <div style="display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;align-items:flex-start">',
    '    <div style="min-width:0">',
    '      <p class="eyebrow" style="margin-bottom:6px">任务 ' + job.id + '</p>',
    '      <h1 class="hero" style="font-size:23px;margin-bottom:6px">' + esc(job.filename) + '</h1>',
    '      <p class="hint">' + (job.pages || '—') + ' 页 · ' + fmtSize(job.size) + ' · ' +
         (job.opts.tgt_lang === 'zh' ? '译为简体中文' : '译为 English') + ' · ' + fmtDate(job.created_at) + '</p>',
    '    </div>',
    '    <div class="actions" style="margin:0">',
    '      <a class="btn" href="/api/jobs/' + job.id + '/source" target="_blank" rel="noopener">查看原文</a>',
          read, again, dl,
    '    </div>',
    '  </div>',
    '</div>',
    '<div class="card" id="progCard">',
    '  <div style="display:flex;justify-content:space-between;align-items:baseline">',
    '    <h2 style="margin:0"><span id="progState">' + (err ? '任务失败' : done ? '任务完成' : '正在处理') + '</span> <span class="sub" id="stageLabel">' + esc(job.stage_label || '') + '</span></h2>',
    '    <span class="mono" id="progPct">' + (job.progress || 0) + '%</span>',
    '  </div>',
    '  <div class="stepper">' + stepperHtml(job) + '</div>',
    '  <div class="bar"><i id="progBar" style="width:' + (job.progress || 0) + '%"></i></div>',
    '  <div class="progress-meta"><span id="progMsg">' + esc(job.message || '') + '</span><span id="progMeta"></span></div>',
    err ? '<div class="log" style="color:#8f3535">' + esc(job.error || '未知错误') + '</div>' : '<div class="log" id="progLog"></div>',
    '</div>',
    '<div id="resultCard"></div>',
    '<div id="blocksCard"></div>'
  ].join('\n');
  $('#jobBody').innerHTML = body;
  var b;
  b = $('#jobRead');    if (b) b.addEventListener('click', function () { openReader(job.id, 'out'); });
  b = $('#jobReadCmp'); if (b) b.addEventListener('click', function () { openReader(job.id, 'cmp'); });
  b = $('#jobReadSrc'); if (b) b.addEventListener('click', function () { openReader(job.id, 'src'); });
  updateProgress(job);
  if (done) renderResult(job);
}

function updateProgress(job) {
  var pct = Math.max(0, Math.min(100, job.progress || 0));
  var bar = $('#progBar'); if (bar) bar.style.width = pct + '%';
  var p = $('#progPct'); if (p) p.textContent = pct.toFixed(0) + '%';
  var lab = $('#stageLabel');
  if (lab) lab.textContent = (job.status === 'done' || job.status === 'error') ? '' : (job.stage_label || '');
  var st = $('#progState');
  if (st) st.textContent = job.status === 'done' ? '任务完成' : (job.status === 'error' ? '任务失败' : '正在处理');
  var msg = $('#progMsg'); if (msg) msg.textContent = job.message || '';
  var meta = $('#progMeta');
  if (meta) {
    var bits = [];
    if (job.stats && job.stats.rebuild) bits.push('溢出 ' + job.stats.rebuild.overflow);
    if (job.tokens) bits.push('tokens ' + ((job.tokens.in || 0) + (job.tokens.out || 0)));
    if (job.units) bits.push(job.units + ' 块');
    meta.textContent = bits.join(' · ');
  }
  var done = job.status === 'done';
  var idx = -1;
  STEPS.forEach(function (s, i) { if (s[0] === job.stage) idx = i; });
  $$('.step').forEach(function (el, i) {
    var cls = 'step';
    if (done || idx > i) cls += ' done';
    else if (idx === i) cls += ' act';
    el.className = cls;
  });
  var log = $('#progLog');
  if (log && job.log && job.log.length) {
    log.innerHTML = job.log.slice(-40).map(function (l) {
      return '<div>' + new Date(l.t * 1000).toLocaleTimeString('zh-CN', { hour12: false }) + '  ' + esc(l.text) + '</div>';
    }).join('');
    log.scrollTop = log.scrollHeight;
  }
}

function startPoll(id) {
  stopPoll();
  function tick() {
    api('/api/jobs/' + id).then(function (job) {
      state.job = job;
      updateProgress(job);
      if (job.status === 'done' || job.status === 'error') {
        stopPoll();
        if (job.status === 'done') { state.ver = Date.now(); renderJob(job); }
        else { renderJob(job); }
      }
    }).catch(function () { /* 忽略抖动 */ });
  }
  tick();
  state.poll = setInterval(tick, 1300);
}

function renderResult(job) {
  var qa = job.qa || {};
  function chip(k, v, cls) {
    return '<span class="chip ' + (cls || '') + '"><span class="k">' + k + '</span>' + v + '</span>';
  }
  var chips = [
    chip('页数', job.pages || '—'),
    chip('版面块', job.units || 0),
    chip('用时', (job.duration || 0) + ' s'),
    chip('溢出', (qa.overflow == null ? 0 : qa.overflow), qa.overflow ? 'warn' : 'ok'),
    chip('压图', (qa.over_image == null ? 0 : qa.over_image), qa.over_image ? 'warn' : 'ok'),
    chip('跳过', job.skipped == null ? 0 : job.skipped),
    job.tokens ? chip('用量', ((job.tokens.in || 0) + (job.tokens.out || 0)).toLocaleString() + ' tokens') : ''
  ].join('');

  var thumbs = '';
  if (job.pages) {
    var arr = [], n = Math.min(job.pages, 60);
    for (var i = 1; i <= n; i++) {
      var lazy = i > 12 ? ' loading="lazy"' : '';
      arr.push('<div class="thumb" data-p="' + i + '">' +
        '<img' + lazy + ' src="/api/jobs/' + job.id + '/page/' + i + '?dpi=100&src=out&v=' + state.ver + '" alt="第 ' + i + ' 页">' +
        '<span class="pn">' + i + '</span></div>');
    }
    thumbs = '<div class="grid">' + arr.join('') + '</div>';
  }

  var html = [
    '<div class="card">',
    '  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap">',
    '    <h2 style="margin:0">成片预览 <span class="sub">点击放大 · 版面与原文逐点对齐</span></h2>',
    '    <div class="actions" style="margin:0">',
    '      <button class="btn small primary" id="readOut">在线阅读</button>',
    '      <button class="btn small" id="readCmp">中英对比</button>',
    '      <a class="btn small" href="/api/jobs/' + job.id + '/download">下载 PDF</a>',
    '      <button class="btn small ghost" id="toggleBlocks">双语校对</button>',
    '    </div>',
    '  </div>',
    '  <div class="chips">' + chips + '</div>',
    thumbs,
    '</div>'
  ].join('\n');

  var host = $('#resultCard');
  if (!host) return;
  host.innerHTML = html;
  $$('#resultCard .thumb').forEach(function (t) {
    t.addEventListener('click', function () { openLb(Number(t.dataset.p), job.pages); });
  });
  var ro = $('#readOut'); if (ro) ro.addEventListener('click', function () { openReader(job.id, 'out'); });
  var rc = $('#readCmp'); if (rc) rc.addEventListener('click', function () { openReader(job.id, 'cmp'); });
  var tb = $('#toggleBlocks');
  if (tb) tb.addEventListener('click', function () {
    if (state.blocks) { renderBlocks(); return; }
    loadBlocks(job.id).then(function () {
      var c = $('#blocksCard');
      if (c) c.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

/* ------------------------------ 预览 ------------------------------ */
function openLb(page, total) {
  state.lbIdx = page; state.lbPages = total || page;
  $('#lbImg').src = '/api/jobs/' + state.jobId + '/page/' + page + '?dpi=170&v=' + state.ver;
  $('#lbCap').textContent = '第 ' + page + ' / ' + state.lbPages + ' 页';
  $('#lb').classList.add('show');
}
function lbMove(d) {
  if (!state.lbPages) return;
  var p = state.lbIdx + d;
  if (p < 1) p = 1;
  if (p > state.lbPages) p = state.lbPages;
  openLb(p, state.lbPages);
}
function setupLb() {
  $('#lb').addEventListener('click', function (e) { if (e.target.id === 'lb') $('#lb').classList.remove('show'); });
  $('#lbPrev').addEventListener('click', function () { lbMove(-1); });
  $('#lbNext').addEventListener('click', function () { lbMove(1); });
}

/* ================= 在线阅读器 ================= */
/* mode: 'src' 原文 · 'out' 译文 · 'cmp' 双栏对比 */
var rd = { job: null, mode: 'out', zoom: 1, fit: 820, page: 1, dpi: 130, syncing: false, pendingPage: null };

function rdDpi() {
  var z = rd.zoom;
  return z <= 1.05 ? 130 : (z <= 1.55 ? 170 : 220);
}
function rdPagesHtml(which) {
  var arr = [];
  for (var i = 1; i <= rd.job.pages; i++) {
    arr.push('<div class="rd-page" data-p="' + i + '">' +
      '<img loading="lazy" onload="rdImgOk(this)" alt="第 ' + i + ' 页" ' +
      'src="/api/jobs/' + rd.job.id + '/page/' + i + '?dpi=' + rd.dpi + '&src=' + which + '">' +
      '<span class="rd-num">' + i + '</span></div>');
  }
  return arr.join('');
}

/* 图片加载完成后，若正是跳转目标页则再锚定一次，修正占位高度带来的偏移 */
function rdImgOk(img) {
  img.classList.add('ok');
  if (!rd.pendingPage) return;
  var page = img.closest('.rd-page');
  if (page && Number(page.dataset.p) === rd.pendingPage) {
    var pane = img.closest('.rd-pane');
    pane.scrollTop = page.offsetTop - 10;
    rd.pendingPage = null;
  }
}

function rdRender() {
  var cmp = rd.mode === 'cmp';
  var paneA = $('#rdPaneA'), paneB = $('#rdPaneB');
  $('#rdBody').classList.toggle('cmp', cmp);
  var aWhich = rd.mode === 'src' ? 'source' : 'out';
  paneA.innerHTML = (cmp ? '<div class="rd-tag">原文 · English</div>' : '') + rdPagesHtml(aWhich);
  if (cmp) {
    paneB.style.display = '';
    paneB.innerHTML = '<div class="rd-tag">译文 · 中文</div>' + rdPagesHtml('out');
  } else {
    paneB.style.display = 'none';
    paneB.innerHTML = '';
  }
  $$('#rdMode button').forEach(function (b) { b.classList.toggle('on', b.dataset.mode === rd.mode); });
  rdApplyWidth();
  rdJump(rd.page);
}

function rdApplyWidth() {
  var w = Math.max(280, Math.round(rd.fit * rd.zoom));
  $('#rdPaneA').style.setProperty('--pw', w + 'px');
  $('#rdPaneB').style.setProperty('--pw', w + 'px');
  $('#rdZoomShow').textContent = Math.round(rd.zoom * 100) + '%';
}

function rdFitWidth() {
  var pane = $('#rdPaneA');
  rd.zoom = 1;
  rd.fit = Math.max(280, pane.clientWidth - 40);
  rdApplyWidth();
}

function rdSetZoom(z) {
  rd.zoom = Math.max(0.5, Math.min(2.5, z));
  var dpi = rdDpi();
  if (dpi !== rd.dpi) {
    rd.dpi = dpi;
    $$('.rd-pane img').forEach(function (img) {
      img.src = img.src.replace(/dpi=\d+/, 'dpi=' + dpi);
    });
  }
  rdApplyWidth();
}

function rdUpdatePageUI() {
  $('#rdPageInput').value = rd.page;
  $('#rdPageShow').textContent = '/ ' + rd.job.pages;
}

function rdTrackPage(pane) {
  if (!pane || !pane.innerHTML) return;
  var st = pane.scrollTop, best = 1, bestD = 1e9;
  $$('.rd-page', pane).forEach(function (p) {
    var d = Math.abs(p.offsetTop - st);
    if (d < bestD) { bestD = d; best = Number(p.dataset.p); }
  });
  rd.page = best;
  rdUpdatePageUI();
}

function rdJump(p) {
  p = Math.max(1, Math.min(rd.job.pages, p || 1));
  rd.page = p;
  rd.pendingPage = p;
  setTimeout(function () { rd.pendingPage = null; }, 900);
  [$('#rdPaneA'), $('#rdPaneB')].forEach(function (pane) {
    if (!pane || !pane.innerHTML) return;
    var el = pane.querySelector('.rd-page[data-p="' + p + '"]');
    if (el) pane.scrollTop = el.offsetTop - 10;
  });
  rdUpdatePageUI();
}

function rdSetMode(m) {
  if (rd.mode === m) return;
  rd.mode = m;
  rdRender();
  rdFitWidth();
}

function openReader(jobId, mode) {
  api('/api/jobs/' + jobId).then(function (job) {
    rd.job = job;
    rd.page = 1;
    var hasOut = job.status === 'done';
    rd.mode = (!hasOut && mode !== 'src') ? 'src' : (mode || 'out');
    rd.dpi = 130; rd.zoom = 1;
    $('#rdTitle').textContent = job.filename + '（' + job.pages + ' 页）';
    $$('#rdMode button').forEach(function (b) {
      b.disabled = (b.dataset.mode !== 'src' && !hasOut);
    });
    document.body.classList.add('rd-open');
    $('#reader').classList.add('show');
    rdRender();
    rdFitWidth();
    rdUpdatePageUI();
  }).catch(function (e) { toast('打开阅读器失败：' + e.message); });
}

function closeReader() {
  if (!$('#reader').classList.contains('show')) return;
  $('#reader').classList.remove('show');
  document.body.classList.remove('rd-open');
  $('#rdPaneA').innerHTML = '';
  $('#rdPaneB').innerHTML = '';
}

function setupReader() {
  $('#rdClose').addEventListener('click', closeReader);
  $('#rdPrev').addEventListener('click', function () { rdJump(rd.page - 1); });
  $('#rdNext').addEventListener('click', function () { rdJump(rd.page + 1); });
  $('#rdZoomIn').addEventListener('click', function () { rdSetZoom(rd.zoom + 0.15); });
  $('#rdZoomOut').addEventListener('click', function () { rdSetZoom(rd.zoom - 0.15); });
  $('#rdFit').addEventListener('click', function () { rdFitWidth(); });
  $$('#rdMode button').forEach(function (b) {
    b.addEventListener('click', function () { if (!b.disabled) rdSetMode(b.dataset.mode); });
  });
  $('#rdPageInput').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { rdJump(parseInt(this.value, 10) || 1); this.blur(); }
  });
  $('#rdPageInput').addEventListener('blur', function () { rdUpdatePageUI(); });

  [$('#rdPaneA'), $('#rdPaneB')].forEach(function (pane) {
    pane.addEventListener('scroll', function () {
      if (rd.mode !== 'cmp') { rdTrackPage(pane); return; }
      if (rd.syncing) return;
      rd.syncing = true;
      var other = (pane === $('#rdPaneA')) ? $('#rdPaneB') : $('#rdPaneA');
      var sMax = pane.scrollHeight - pane.clientHeight;
      var oMax = other.scrollHeight - other.clientHeight;
      if (sMax > 0 && oMax > 0) other.scrollTop = pane.scrollTop / sMax * oMax;
      requestAnimationFrame(function () { rd.syncing = false; });
      rdTrackPage(pane);
    });
  });

  window.addEventListener('resize', debounce(function () {
    if (!$('#reader').classList.contains('show')) return;
    rd.fit = Math.max(280, $('#rdPaneA').clientWidth - 40);
    rdApplyWidth();
  }, 150));
}

/* ------------------------------ 双语校对 ------------------------------ */
function loadBlocks(id) {
  return api('/api/jobs/' + id + '/blocks').then(function (data) {
    state.blocks = data;
    renderBlocks();
  }).catch(function () { /* 尚未生成 */ });
}

function renderBlocks() {
  var host = $('#blocksCard');
  if (!host || !state.blocks) return;
  host.dataset.loaded = '1';
  var blocks = state.blocks.blocks || [];
  var skipped = state.blocks.skipped || [];
  var needle = state.filter.trim().toLowerCase();
  var list = blocks.filter(function (b) {
    if (!needle) return true;
    return (b.text || '').toLowerCase().indexOf(needle) >= 0 || (b.zh || '').indexOf(needle) >= 0;
  });

  var items = list.map(function (b) {
    var kind = b.style === 'heading' ? '标题' : (b.style === 'subheading' ? '次级标题' : '正文');
    return [
      '<div class="blk" data-id="' + b.id + '">',
      '  <div class="head"><span class="tag">' + b.id + '</span><span>第 ' + b.page + ' 页</span><span>' + kind + '</span></div>',
      '  <div class="tw">',
      '    <div class="en">' + esc(b.text) + '</div>',
      '    <textarea spellcheck="false">' + esc(b.zh) + '</textarea>',
      '  </div>',
      '  <div class="foot"><span>' + (b.zh || '').length + ' 字</span><button class="btn small save" disabled>保存</button></div>',
      '</div>'
    ].join('');
  }).join('');

  var skipRows = skipped.slice(0, 200).map(function (s) {
    return '<div>' + esc(s.id) + '  [' + esc(s.reason) + ']  ' + esc((s.text || '').slice(0, 70)) + '</div>';
  }).join('');

  host.innerHTML = [
    '<div class="card" id="blkCard">',
    '  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:14px;flex-wrap:wrap">',
    '    <h2 style="margin:0">双语校对 <span class="sub">左原文右译文 · 改动后点「应用到 PDF」重新排版</span></h2>',
    '    <div class="actions" style="margin:0"><button class="btn small" id="rebuildBtn">应用到 PDF</button></div>',
    '  </div>',
    '  <div class="blk-toolbar">',
    '    <input type="text" class="search" id="blkSearch" placeholder="搜索原文或译文…" value="' + esc(state.filter) + '">',
    '    <span class="mono" id="blkCount">' + list.length + ' 条</span>',
    '  </div>',
    '  <div class="blklist" id="blkList">' + items + '</div>',
    '  <details style="margin-top:14px">',
    '    <summary style="cursor:pointer;font-size:12.5px;color:var(--muted)">跳过的 ' + skipped.length + ' 个版块（页眉页脚 / 参考文献 / 作者单位 / 公式…）</summary>',
    '    <div class="log" style="max-height:220px;margin-top:10px">' + skipRows + '</div>',
    '  </details>',
    '</div>'
  ].join('\n');

  var search = $('#blkSearch');
  search.addEventListener('input', debounce(function () { state.filter = search.value; renderBlocks(); }, 260));

  $$('#blkList .blk').forEach(function (el) {
    var ta = el.querySelector('textarea');
    var btn = el.querySelector('.save');
    ta.addEventListener('input', function () {
      el.classList.add('dirty');
      btn.disabled = false;
    });
    btn.addEventListener('click', function () { saveBlock(el.dataset.id, ta.value, el, btn); });
  });
  var rb = $('#rebuildBtn');
  if (rb) rb.addEventListener('click', doRebuild);
}

function debounce(fn, ms) {
  var t;
  return function () {
    var args = arguments;
    clearTimeout(t);
    t = setTimeout(function () { fn.apply(null, args); }, ms);
  };
}

function saveBlock(id, text, el, btn) {
  btn.disabled = true; btn.textContent = '保存中…';
  api('/api/jobs/' + state.jobId + '/blocks/' + id, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ zh: text })
  }).then(function () {
    el.classList.remove('dirty');
    btn.textContent = '已保存';
    toast('已保存，点「应用到 PDF」重新排版');
    setTimeout(function () { btn.textContent = '保存'; }, 1800);
  }).catch(function (e) {
    btn.textContent = '保存'; btn.disabled = false;
    toast('保存失败：' + e.message);
  });
}

function doRebuild() {
  var btn = $('#rebuildBtn');
  btn.disabled = true; btn.textContent = '重新排版中…';
  api('/api/jobs/' + state.jobId + '/rebuild', { method: 'POST' }).then(function () {
    var wait = setInterval(function () {
      api('/api/jobs/' + state.jobId).then(function (j) {
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(wait);
          state.ver = Date.now();
          btn.disabled = false; btn.textContent = '应用到 PDF';
          if (j.status === 'error') { toast('排版失败：' + (j.error || '')); return; }
          renderResult(j);
          toast('已重新排版，预览已更新');
        }
      }).catch(function () { /* ignore */ });
    }, 1200);
  }).catch(function (e) {
    btn.disabled = false; btn.textContent = '应用到 PDF';
    toast('失败：' + e.message);
  });
}

/* ------------------------------ 记录 ------------------------------ */
function loadHistory() {
  var body = $('#jobsBody');
  api('/api/jobs').then(function (jobs) {
    body.innerHTML = jobs.map(function (j) {
      var cls = j.status === 'done' ? 'done' : (j.status === 'error' ? 'err' : 'run');
      var dl = j.status === 'done' ? '<a class="btn small" href="/api/jobs/' + j.id + '/download">下载</a>' : '';
      var read = j.status === 'done'
        ? '<button class="btn small" data-read="' + j.id + '" title="打开阅读器：中英对比">阅读</button>' : '';
      return [
        '<tr>',
        '  <td><div class="fname" data-id="' + j.id + '">' + esc(j.filename) + '</div><div class="mono">' + j.id + '</div></td>',
        '  <td>' + (j.pages || '—') + '</td>',
        '  <td>' + (j.units == null ? '—' : j.units) + '</td>',
        '  <td><span class="st ' + cls + '">' + (STATUS_TEXT[j.status] || j.status) + '</span></td>',
        '  <td class="mono">' + fmtDate(j.created_at) + '</td>',
        '  <td style="text-align:right">' + read + dl +
        '    <button class="btn small ghost" data-open="' + j.id + '">打开</button>' +
        '    <button class="btn small ghost" data-del="' + j.id + '">删除</button></td>',
        '</tr>'
      ].join('');
    }).join('');
    $('#jobsEmpty').style.display = jobs.length ? 'none' : 'block';
    $$('#jobsBody [data-read]').forEach(function (b) {
      b.addEventListener('click', function () { openReader(b.dataset.read, 'cmp'); });
    });
    $$('#jobsBody [data-open]').forEach(function (b) {
      b.addEventListener('click', function () { location.hash = '#/job/' + b.dataset.open; });
    });
    $$('#jobsBody .fname').forEach(function (b) {
      b.addEventListener('click', function () { location.hash = '#/job/' + b.dataset.id; });
    });
    $$('#jobsBody [data-del]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (!confirm('删除这条记录及其生成的文件？')) return;
        api('/api/jobs/' + b.dataset.del, { method: 'DELETE' }).then(function () {
          loadHistory(); toast('已删除');
        }).catch(function (e) { toast('删除失败：' + e.message); });
      });
    });
  }).catch(function (e) {
    body.innerHTML = '<tr><td colspan="6" class="empty">读取失败：' + esc(e.message) + '</td></tr>';
  });
}

/* ------------------------------ 设置 ------------------------------ */
function loadSettings() {
  api('/api/settings').then(function (s) {
    $('#setModel').value = s.model || 'deepseek-chat';
    $('#setConc').value = s.concurrency || 4;
    $('#setBatch').value = s.batch_chars || 2600;
    $('#setBase').value = s.base_url || 'https://api.deepseek.com';
    if (s.data_dir && $('#dataPath')) $('#dataPath').textContent = s.data_dir;
    $('#keyDesc').textContent = s.has_key
      ? '已配置：' + (s.key_hint || '（本机凭据）')
      : '未检测到 Key —— 请填写，或在 ~/.dsh/.credentials.yaml 中提供 DEEPSEEK_API_KEY';
  }).catch(function (e) { toast('读取设置失败：' + e.message); });
}

function saveSettings() {
  var patch = {
    model: $('#setModel').value,
    concurrency: Number($('#setConc').value) || 4,
    batch_chars: Number($('#setBatch').value) || 2600,
    base_url: $('#setBase').value.trim() || 'https://api.deepseek.com'
  };
  var key = $('#setKey').value.trim();
  if (key) patch.api_key = key;
  api('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch)
  }).then(function () {
    $('#setKey').value = '';
    toast('设置已保存');
    loadSettings();
    checkHealth();
  }).catch(function (e) { toast('保存失败：' + e.message); });
}

/* ------------------------------ 健康检查 ------------------------------ */
function checkHealth() {
  api('/api/health').then(function (h) {
    $('#healthDot').className = 'dot ' + (h.key ? 'ok' : 'bad');
    $('#healthText').textContent = h.key ? ('接口就绪 · ' + h.model) : '缺少 API Key';
  }).catch(function () {
    $('#healthDot').className = 'dot bad';
    $('#healthText').textContent = '服务未响应';
  });
}

/* ------------------------------ 启动 ------------------------------ */
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', function () {
  setupUpload();
  setupLb();
  setupReader();
  $('#go').addEventListener('click', submitJob);
  $('#saveSettings').addEventListener('click', saveSettings);
  document.addEventListener('keydown', function (e) {
    var rdOpen = $('#reader').classList.contains('show');
    if (rdOpen) {
      if (e.key === 'Escape') closeReader();
      else if (e.key === 'ArrowLeft') rdJump(rd.page - 1);
      else if (e.key === 'ArrowRight') rdJump(rd.page + 1);
      else if (e.key === '+' || e.key === '=') rdSetZoom(rd.zoom + 0.15);
      else if (e.key === '-' || e.key === '_') rdSetZoom(rd.zoom - 0.15);
      return;
    }
    var open = $('#lb').classList.contains('show');
    if (e.key === 'Escape') $('#lb').classList.remove('show');
    if (open && e.key === 'ArrowLeft') lbMove(-1);
    if (open && e.key === 'ArrowRight') lbMove(1);
  });
  checkHealth();
  route();
});
