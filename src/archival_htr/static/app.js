// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function threeState(val) {
  if (val === 'true') return true;
  if (val === 'false') return false;
  return null;
}
function showSpinner(id) {
  document.getElementById(id).innerHTML = '<div class="spinner"></div>';
}
function showError(id, msg) {
  document.getElementById(id).innerHTML = '<div class="error">' + esc(msg) + '</div>';
}
async function apiFetch(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    const isReview   = btn.dataset.tab === 'review';
    const isOverview = btn.dataset.tab === 'overview';
    document.querySelector('main').classList.toggle('review-active', isReview || isOverview);
    if (isReview   && !rvLoaded) { rvLoadCollections(); rvLoaded = true; }
    if (isOverview && !ovLoaded) { ovLoad(); ovLoaded = true; }
  });
});

// ── Slider labels ─────────────────────────────────────────────────────────────
document.getElementById('s-n').addEventListener('input', e => {
  document.getElementById('s-n-val').textContent = e.target.value;
});
document.getElementById('a-n').addEventListener('input', e => {
  document.getElementById('a-n-val').textContent = e.target.value;
});

// ── Query source list ─────────────────────────────────────────────────────────
async function loadSources() {
  const sel = document.getElementById('q-source');
  const btn = document.getElementById('q-submit');
  try {
    const resp = await fetch('/api/sources');
    const data = await resp.json();
    if (data.sources.length === 0) {
      sel.innerHTML = '<option value="">— no sources indexed yet —</option>';
      btn.disabled = true;
    } else {
      sel.innerHTML = '<option value="">Select a document…</option>' +
        data.sources.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('');
      sel.addEventListener('change', () => { btn.disabled = !sel.value; });
      btn.disabled = true;
    }
  } catch {
    sel.innerHTML = '<option value="">Failed to load sources</option>';
  }
}
loadSources();
document.getElementById('tab-query') &&
  document.querySelector('[data-tab=query]').addEventListener('click', loadSources);

// ── Search ────────────────────────────────────────────────────────────────────
document.getElementById('search-form').addEventListener('submit', async e => {
  e.preventDefault();
  showSpinner('search-results');
  const body = {
    query: document.getElementById('s-query').value.trim(),
    n: parseInt(document.getElementById('s-n').value),
    source: document.getElementById('s-source').value.trim() || null,
    job_application: threeState(document.getElementById('s-job').value),
    military_service: threeState(document.getElementById('s-mil').value),
  };
  try {
    const data = await apiFetch('/api/search', body);
    const resultsEl = document.getElementById('search-results');
    if (data.count === 0) {
      resultsEl.innerHTML = '<div class="empty">No results found.</div>';
      return;
    }
    resultsEl.innerHTML = data.results.map(r => {
      const pct = Math.round(r.score * 100);
      const excerpt = esc(r.text.length > 400 ? r.text.slice(0, 400) + '…' : r.text);
      return '<div class="result-card">' +
        '<div class="card-header">' +
          '<span class="badge">' + esc(r.source) + '</span>' +
          '<div class="score-track"><div class="score-bar" style="width:' + pct + '%"></div></div>' +
          '<span class="score-label">' + r.score.toFixed(3) + '</span>' +
        '</div>' +
        '<div class="excerpt">' + excerpt + '</div>' +
        '</div>';
    }).join('');
  } catch (err) {
    showError('search-results', err.message);
  }
});

// ── Ask ───────────────────────────────────────────────────────────────────────
document.getElementById('ask-form').addEventListener('submit', async e => {
  e.preventDefault();
  showSpinner('ask-results');
  const body = {
    question: document.getElementById('a-question').value.trim(),
    n: parseInt(document.getElementById('a-n').value),
    source: document.getElementById('a-source').value.trim() || null,
    job_application: threeState(document.getElementById('a-job').value),
    military_service: threeState(document.getElementById('a-mil').value),
  };
  try {
    const data = await apiFetch('/api/ask', body);
    const tags = data.sources.map(s => '<span class="meta-tag">' + esc(s) + '</span>').join('');
    document.getElementById('ask-results').innerHTML =
      '<div class="answer-box">' +
        '<div class="answer-meta">' +
          '<span>' + data.chunks_used + ' passage' + (data.chunks_used !== 1 ? 's' : '') + ' consulted</span>' +
          tags +
        '</div>' +
        '<div class="answer-text">' + esc(data.answer) + '</div>' +
      '</div>';
  } catch (err) {
    showError('ask-results', err.message);
  }
});

// ── Ingest ────────────────────────────────────────────────────────────────────
let _ingestPoll = null;
let _ingestLogOffset = 0;

function setIngestStatus(msg, colour) {
  document.getElementById('ingest-status').innerHTML =
    '<span style="color:' + colour + ';font-weight:600;">' + esc(msg) + '</span>';
}

function appendLog(lines) {
  const pre = document.getElementById('ingest-log');
  pre.style.display = 'block';
  pre.textContent += lines.join('\n') + (lines.length ? '\n' : '');
  pre.scrollTop = pre.scrollHeight;
}

async function pollIngest() {
  try {
    const resp = await fetch('/api/ingest/status');
    const data = await resp.json();
    appendLog(data.log.slice(_ingestLogOffset));
    _ingestLogOffset = data.log.length;
    if (!data.running) {
      clearInterval(_ingestPoll);
      _ingestPoll = null;
      document.getElementById('i-submit').disabled = false;
      if (data.exit_code === 0) {
        setIngestStatus('Ingest completed successfully.', 'var(--success)');
        loadSources();
      } else {
        setIngestStatus('Ingest finished with errors (exit code ' + data.exit_code + ').', 'var(--accent)');
      }
    }
  } catch { /* ignore poll errors */ }
}

document.getElementById('ingest-form').addEventListener('submit', async e => {
  e.preventDefault();
  if (_ingestPoll) return;
  const docVal = document.getElementById('i-doc').value.trim();
  const body = {
    overwrite: document.getElementById('i-overwrite').checked,
    doc: docVal ? docVal.split(',').map(s => s.trim()).filter(Boolean) : [],
  };
  document.getElementById('i-submit').disabled = true;
  document.getElementById('ingest-log').textContent = '';
  document.getElementById('ingest-log').style.display = 'none';
  _ingestLogOffset = 0;
  setIngestStatus('Starting ingest…', 'var(--warn)');
  try {
    const resp = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const d = await resp.json();
      setIngestStatus(d.detail || 'Failed to start.', 'var(--accent)');
      document.getElementById('i-submit').disabled = false;
      return;
    }
    setIngestStatus('Running…', 'var(--warn)');
    _ingestPoll = setInterval(pollIngest, 2000);
  } catch (err) {
    setIngestStatus('Error: ' + err.message, 'var(--accent)');
    document.getElementById('i-submit').disabled = false;
  }
});

// ── Query ─────────────────────────────────────────────────────────────────────
document.getElementById('query-form').addEventListener('submit', async e => {
  e.preventDefault();
  showSpinner('query-results');
  const body = {
    question: document.getElementById('q-question').value.trim(),
    source: document.getElementById('q-source').value,
  };
  try {
    const data = await apiFetch('/api/query', body);
    document.getElementById('query-results').innerHTML =
      '<div class="answer-box">' +
        '<div class="answer-meta">' +
          '<span>Full transcript: <span class="meta-tag">' + esc(data.source) + '</span></span>' +
          '<span>' + data.context_chars.toLocaleString() + ' chars in context</span>' +
        '</div>' +
        '<div class="answer-text">' + esc(data.answer) + '</div>' +
      '</div>';
  } catch (err) {
    showError('query-results', err.message);
  }
});

// ── Review ────────────────────────────────────────────────────────────────────
let rvLoaded = false;
let rvPages = [];
let rvPageIdx = 0;
let rvCurrentCollection = '';
let rvCurrentDocument = '';
let rvZoom = 1.0;
let rvZoomMin = 0.1;
let rvImgNaturalW = 0;
let rvImgNaturalH = 0;
const RV_ZOOM_STEP = 0.25;
const RV_ZOOM_MAX  = 4.0;
function rvApplyZoom() {
  const panel = document.getElementById('rv-image-panel');
  const wrap  = panel && panel.querySelector('.rv-img-wrap');
  const img   = wrap  && wrap.querySelector('img');
  if (!img || !rvImgNaturalW) return;
  const vw = rvImgNaturalW * rvZoom;
  const vh = rvImgNaturalH * rvZoom;
  wrap.style.width  = vw + 'px';
  wrap.style.height = vh + 'px';
  img.style.transform = 'scale(' + rvZoom + ')';
}
function rvComputeZoomMin() {
  const panel = document.getElementById('rv-image-panel');
  const img = panel.querySelector('img');
  if (!img || !img.naturalWidth) return;
  rvImgNaturalW = img.naturalWidth;
  rvImgNaturalH = img.naturalHeight;
  rvZoomMin = Math.min(panel.clientWidth / rvImgNaturalW, panel.clientHeight / rvImgNaturalH);
  rvZoom = Math.max(rvZoomMin, (panel.clientHeight * 0.6) / rvImgNaturalH);
  rvApplyZoom();
  panel.scrollLeft = Math.max(0, (rvImgNaturalW * rvZoom - panel.clientWidth)  / 2);
  panel.scrollTop  = Math.max(0, (rvImgNaturalH * rvZoom - panel.clientHeight) / 2);
}

async function rvLoadCollections() {
  const sel = document.getElementById('rv-collection');
  try {
    const resp = await fetch('/api/collections');
    const data = await resp.json();
    if (!data.collections || data.collections.length === 0) {
      sel.innerHTML = '<option value="">— no collections —</option>';
    } else {
      sel.innerHTML = '<option value="">Select collection…</option>' +
        data.collections.map(c => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join('');
    }
  } catch {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

async function rvSelectCollection(col) {
  rvCurrentCollection = col;
  rvCurrentDocument = '';
  rvPages = [];
  rvPageIdx = 0;
  rvClearPanels();
  rvUpdateNav();
  const docSel = document.getElementById('rv-document');
  docSel.innerHTML = '<option value="">—</option>';
  docSel.disabled = true;
  if (!col) return;
  try {
    const resp = await fetch('/api/collections/' + encodeURIComponent(col) + '/documents');
    const data = await resp.json();
    if (data.documents && data.documents.length > 0) {
      docSel.innerHTML = '<option value="">Select document…</option>' +
        data.documents.map(d => '<option value="' + esc(d) + '">' + esc(d) + '</option>').join('');
      docSel.disabled = false;
    } else {
      docSel.innerHTML = '<option value="">— no documents —</option>';
    }
  } catch {
    docSel.innerHTML = '<option value="">Failed to load</option>';
  }
}

async function rvSelectDocument(doc) {
  rvCurrentDocument = doc;
  rvPages = [];
  rvPageIdx = 0;
  rvClearPanels();
  if (!doc || !rvCurrentCollection) { rvUpdateNav(); return; }
  try {
    const resp = await fetch(
      '/api/review/' + encodeURIComponent(rvCurrentCollection) +
      '/' + encodeURIComponent(doc) + '/pages'
    );
    rvPages = await resp.json();
    rvPageIdx = 0;
    rvUpdateNav();
    if (rvPages.length > 0) rvLoadPage(0);
  } catch {
    rvPages = [];
    rvUpdateNav();
  }
}

function rvUpdateNav() {
  const label = document.getElementById('rv-page-label');
  const prev = document.getElementById('rv-prev');
  const next = document.getElementById('rv-next');
  if (rvPages.length === 0) {
    label.textContent = '—';
    prev.disabled = true;
    next.disabled = true;
    return;
  }
  label.textContent = (rvPageIdx + 1) + ' / ' + rvPages.length + '  —  ' + rvPages[rvPageIdx].stem;
  prev.disabled = rvPageIdx === 0;
  next.disabled = rvPageIdx === rvPages.length - 1;
}

function rvClearPanels() {
  document.getElementById('rv-image-panel').innerHTML = '<div class="empty">—</div>';
  document.getElementById('rv-imported-panel').innerHTML = '<div class="empty">—</div>';
  document.getElementById('rv-transcribed-panel').innerHTML = '<div class="empty">—</div>';
  const ed = document.getElementById('rv-editor');
  ed.value = '';
  ed.disabled = true;
  document.getElementById('rv-save').disabled = true;
  document.getElementById('rv-save-status').textContent = '';
  rvRenderMeta(null);
}

function rvRenderMeta(meta) {
  const strip = document.getElementById('rv-meta-strip');
  if (!meta) { strip.className = 'hidden'; return; }

  function item(label, valueHtml) {
    return '<span class="rv-meta-item"><span class="rv-meta-label">' +
           esc(label) + '</span>' + valueHtml + '</span>';
  }
  function txt(v)  { return '<span class="rv-meta-value">' + esc(v) + '</span>'; }
  function bool(label, val) {
    if (val === null || val === undefined) return '';
    return item(label, '<span class="rv-meta-bool ' + (val ? 'yes' : 'no') + '">' +
                       (val ? 'Yes' : 'No') + '</span>');
  }

  let html = '';
  if (meta.language)                html += item('Lang',     txt(meta.language));
  if (meta.category)                html += item('Category', txt(meta.category));
  if (meta.date_submission_writing) html += item('Date',     txt(meta.date_submission_writing));
  if (meta.single_page_or_part)     html += item('Scope',    txt(meta.single_page_or_part));
  if (meta.related_to_others)       html += item('Relation', txt(meta.related_to_others));
  html += bool('Job application',  meta.is_job_application);
  html += bool('Military service', meta.military_service_argument);

  strip.innerHTML = html || '<span class="rv-meta-none">No metadata fields available</span>';
  strip.className = '';
}

async function rvLoadPage(idx) {
  rvPageIdx = idx;
  rvUpdateNav();
  const stem = rvPages[idx].stem;
  const base = '/api/review/' + encodeURIComponent(rvCurrentCollection) +
               '/' + encodeURIComponent(rvCurrentDocument) +
               '/' + encodeURIComponent(stem);

  fetch(base + '/metadata')
    .then(r => r.ok ? r.json() : null)
    .then(meta => rvRenderMeta(meta))
    .catch(() => rvRenderMeta(null));

  rvZoom = 1.0;
  rvZoomMin = 0.1;
  rvImgNaturalW = 0;
  rvImgNaturalH = 0;
  const rvImgPanel = document.getElementById('rv-image-panel');
  rvImgPanel.innerHTML = '<div class="rv-img-wrap"><img src="' + base + '/image?t=' + Date.now() + '" alt="page image"></div>';
  rvImgPanel.querySelector('img').addEventListener('load', rvComputeZoomMin);

  const impEl = document.getElementById('rv-imported-panel');
  impEl.innerHTML = '<div class="empty">Loading…</div>';
  fetch(base + '/imported')
    .then(r => r.ok ? r.text() : null)
    .then(t => {
      impEl.innerHTML = t ? '<pre>' + esc(t) + '</pre>' : '<div class="empty">Not available</div>';
    })
    .catch(() => { impEl.innerHTML = '<div class="empty">Error loading</div>'; });

  const tscEl = document.getElementById('rv-transcribed-panel');
  tscEl.innerHTML = '<div class="empty">Loading…</div>';
  let llmText = '';
  try {
    const r = await fetch(base + '/transcribed');
    if (r.ok) {
      llmText = await r.text();
      tscEl.innerHTML = '<pre>' + esc(llmText) + '</pre>';
    } else {
      tscEl.innerHTML = '<div class="empty">Not available</div>';
    }
  } catch {
    tscEl.innerHTML = '<div class="empty">Error loading</div>';
  }

  const ed = document.getElementById('rv-editor');
  document.getElementById('rv-save-status').textContent = '';
  try {
    const r = await fetch(base + '/finalized');
    ed.value = r.ok ? await r.text() : llmText;
  } catch {
    ed.value = llmText;
  }
  ed.disabled = false;
  document.getElementById('rv-save').disabled = false;
}

document.getElementById('rv-zoom-in').addEventListener('click', () => {
  rvZoom = Math.min(RV_ZOOM_MAX, rvZoom + RV_ZOOM_STEP);
  rvApplyZoom();
});
document.getElementById('rv-zoom-out').addEventListener('click', () => {
  rvZoom = Math.max(rvZoomMin, rvZoom - RV_ZOOM_STEP);
  rvApplyZoom();
});
document.getElementById('rv-zoom-reset').addEventListener('click', () => {
  rvZoom = 1.0;
  rvApplyZoom();
});
document.getElementById('rv-image-panel').addEventListener('wheel', e => {
  if (!document.querySelector('#rv-image-panel img')) return;
  e.preventDefault();
  rvZoom = e.deltaY < 0
    ? Math.min(RV_ZOOM_MAX, rvZoom + RV_ZOOM_STEP)
    : Math.max(rvZoomMin, rvZoom - RV_ZOOM_STEP);
  rvApplyZoom();
}, { passive: false });

(function () {
  const panel = document.getElementById('rv-image-panel');
  let dragging = false, startX = 0, startY = 0, scrollX = 0, scrollY = 0;
  panel.addEventListener('mousedown', e => {
    if (e.button !== 0 || !document.querySelector('#rv-image-panel img')) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    scrollX = panel.scrollLeft; scrollY = panel.scrollTop;
    panel.style.cursor = 'grabbing';
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    panel.scrollLeft = scrollX - (e.clientX - startX);
    panel.scrollTop  = scrollY - (e.clientY - startY);
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    panel.style.cursor = '';
  });
})();

document.getElementById('rv-collection').addEventListener('change', e => rvSelectCollection(e.target.value));
document.getElementById('rv-document').addEventListener('change', e => rvSelectDocument(e.target.value));
document.getElementById('rv-prev').addEventListener('click', () => {
  if (rvPageIdx > 0) rvLoadPage(rvPageIdx - 1);
});
document.getElementById('rv-next').addEventListener('click', () => {
  if (rvPageIdx < rvPages.length - 1) rvLoadPage(rvPageIdx + 1);
});

document.getElementById('rv-save').addEventListener('click', async () => {
  if (!rvCurrentCollection || !rvCurrentDocument || rvPages.length === 0) return;
  const stem = rvPages[rvPageIdx].stem;
  const base = '/api/review/' + encodeURIComponent(rvCurrentCollection) +
               '/' + encodeURIComponent(rvCurrentDocument) +
               '/' + encodeURIComponent(stem);
  const statusEl = document.getElementById('rv-save-status');
  statusEl.style.color = '';
  statusEl.textContent = '';
  try {
    const resp = await fetch(base + '/finalized', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: document.getElementById('rv-editor').value }),
    });
    if (resp.ok) {
      statusEl.style.color = 'var(--success)';
      statusEl.textContent = 'Saved ✓';
      rvPages[rvPageIdx].has_finalized = true;
    } else {
      statusEl.style.color = 'var(--accent)';
      statusEl.textContent = 'Save failed';
    }
  } catch {
    statusEl.style.color = 'var(--accent)';
    statusEl.textContent = 'Error saving';
  }
});

// ── Overview ──────────────────────────────────────────────────────────────────
let ovLoaded = false;
let ovPages  = [];

const OV_LANG_CLASS = {
  dutch:   'ov-lang-dutch',
  french:  'ov-lang-french',
  latin:   'ov-lang-latin',
  unknown: 'ov-lang-unknown',
};

const OV_CAT_CLASS = {
  'petitie':             'ov-cat-petitie',
  'sollicitatie':        'ov-cat-sollicitatie',
  'appostille/addendum': 'ov-cat-appostille',
  'rapport':             'ov-cat-rapport',
  'bijlage':             'ov-cat-bijlage',
  'attest':              'ov-cat-attest',
  'andere':              'ov-cat-andere',
};

function ovCellClass(page, colorBy) {
  if (colorBy === 'language') {
    const k = (page.language || 'unknown').toLowerCase();
    return OV_LANG_CLASS[k] || 'ov-lang-other';
  }
  if (colorBy === 'category') {
    const k = (page.category || 'unknown').toLowerCase();
    return OV_CAT_CLASS[k] || 'ov-cat-unknown';
  }
  if (colorBy === 'is_job_application') {
    if (page.is_job_application === true)  return 'ov-bool-true';
    if (page.is_job_application === false) return 'ov-bool-false';
    return 'ov-bool-unknown';
  }
  if (colorBy === 'military_service_argument') {
    if (page.military_service_argument === true)  return 'ov-bool-true';
    if (page.military_service_argument === false) return 'ov-bool-false';
    return 'ov-bool-unknown';
  }
  return 'ov-lang-unknown';
}

function ovGetSwatchClass(colorBy, key) {
  if (colorBy === 'language') return OV_LANG_CLASS[key.toLowerCase()] || 'ov-lang-other';
  if (colorBy === 'category') return OV_CAT_CLASS[key.toLowerCase()]  || 'ov-cat-unknown';
  if (key === 'true')  return 'ov-bool-true';
  if (key === 'false') return 'ov-bool-false';
  return 'ov-bool-unknown';
}

function ovMatchesFilter(page) {
  const fl = document.getElementById('ov-filter-language').value;
  const fc = document.getElementById('ov-filter-category').value;
  const fj = document.getElementById('ov-filter-job').value;
  const fm = document.getElementById('ov-filter-mil').value;
  if (fl && (page.language || '').toLowerCase() !== fl.toLowerCase()) return false;
  if (fc && (page.category || '').toLowerCase() !== fc.toLowerCase()) return false;
  if (fj && page.is_job_application !== (fj === 'true')) return false;
  if (fm && page.military_service_argument !== (fm === 'true')) return false;
  return true;
}

async function ovLoad() {
  const grid = document.getElementById('ov-grid');
  grid.innerHTML = '<div class="spinner"></div>';
  document.getElementById('ov-stats').innerHTML = '';
  try {
    const resp = await fetch('/api/overview');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    ovPages = data.pages;
    ovPopulateCollections();
    ovRender();
  } catch (err) {
    grid.innerHTML = '<div class="error">' + esc(err.message) + '</div>';
  }
}

function ovPopulateCollections() {
  const sel = document.getElementById('ov-collection');
  const prev = sel.value;
  const names = [...new Set(ovPages.map(p => p.collection))].sort();
  if (names.length === 0) {
    sel.innerHTML = '<option value="">— no collections —</option>';
    return;
  }
  sel.innerHTML = names.map(n => '<option value="' + esc(n) + '">' + esc(n) + '</option>').join('');
  sel.value = names.includes(prev) ? prev : names[0];
}

function ovVisiblePages() {
  const col = document.getElementById('ov-collection').value;
  return col ? ovPages.filter(p => p.collection === col) : ovPages;
}

function ovRender() {
  const colorBy = document.getElementById('ov-color-by').value;
  const grid    = document.getElementById('ov-grid');

  if (ovPages.length === 0) {
    grid.innerHTML = '<div class="empty">No metadata available — run an ingest first, then click Refresh.</div>';
    document.getElementById('ov-stats').innerHTML = '';
    return;
  }

  const visible = ovVisiblePages();
  if (visible.length === 0) {
    grid.innerHTML = '<div class="empty">No pages in this collection.</div>';
    document.getElementById('ov-stats').innerHTML = '';
    return;
  }

  function sortKey(p) {
    if (colorBy === 'language')                  return p.language || 'zzz';
    if (colorBy === 'category')                  return p.category || 'zzz';
    if (colorBy === 'is_job_application')        return String(p.is_job_application);
    if (colorBy === 'military_service_argument') return String(p.military_service_argument);
    return p.source_page;
  }

  // Sort: by doc, then by sortKey within doc → cells flow in continuous rows,
  // with same-type pages clustering visually within each document.
  const sorted = [...visible].sort((a, b) => {
    if (a.source_doc !== b.source_doc) return a.source_doc.localeCompare(b.source_doc);
    return sortKey(a).localeCompare(sortKey(b));
  });

  let html = '<div class="ov-cells">';
  let prevDoc = null;
  for (const p of sorted) {
    if (prevDoc !== null && p.source_doc !== prevDoc) {
      html += '<div class="ov-doc-sep" title="' + esc(p.source_doc) + '"></div>';
    }
    prevDoc = p.source_doc;
    const cls = ovCellClass(p, colorBy);
    html += '<div class="ov-cell ' + cls + '"' +
            ' data-col="'  + esc(p.collection)  + '"' +
            ' data-doc="'  + esc(p.source_doc)  + '"' +
            ' data-page="' + esc(p.source_page) + '"' +
            ' data-lang="' + esc(p.language  || '') + '"' +
            ' data-cat="'  + esc(p.category  || '') + '"' +
            ' data-date="' + esc(p.date_submission_writing || '') + '"' +
            ' data-job="'  + (p.is_job_application      === null ? '' : String(p.is_job_application))      + '"' +
            ' data-mil="'  + (p.military_service_argument === null ? '' : String(p.military_service_argument)) + '"' +
            '></div>';
  }
  html += '</div>';
  grid.innerHTML = html;

  // Event delegation — re-attach each render
  grid.addEventListener('mousemove',  ovHandleMouseMove);
  grid.addEventListener('mouseleave', () => { document.getElementById('ov-tooltip').style.display = 'none'; });
  grid.addEventListener('click',      ovHandleClick);

  ovApplyFilter();
}

function ovApplyFilter() {
  const anyFilter = (
    document.getElementById('ov-filter-language').value ||
    document.getElementById('ov-filter-category').value ||
    document.getElementById('ov-filter-job').value ||
    document.getElementById('ov-filter-mil').value
  );

  document.querySelectorAll('.ov-cell').forEach(cell => {
    if (!anyFilter) {
      cell.classList.remove('dimmed', 'highlighted');
      return;
    }
    const pseudo = {
      language:                  cell.dataset.lang || null,
      category:                  cell.dataset.cat  || null,
      is_job_application:        cell.dataset.job === 'true' ? true : cell.dataset.job === 'false' ? false : null,
      military_service_argument: cell.dataset.mil === 'true' ? true : cell.dataset.mil === 'false' ? false : null,
    };
    if (ovMatchesFilter(pseudo)) {
      cell.classList.add('highlighted');
      cell.classList.remove('dimmed');
    } else {
      cell.classList.add('dimmed');
      cell.classList.remove('highlighted');
    }
  });
  ovUpdateStats();
}

function ovUpdateStats() {
  const statsEl = document.getElementById('ov-stats');
  const colorBy = document.getElementById('ov-color-by').value;
  const active  = ovVisiblePages().filter(ovMatchesFilter);

  if (active.length === 0) {
    statsEl.innerHTML = '<span class="rv-meta-none">No pages match the current filter.</span>';
    return;
  }

  const counts = {};
  for (const p of active) {
    let key;
    if      (colorBy === 'language')                  key = p.language || 'unknown';
    else if (colorBy === 'category')                  key = p.category || 'unknown';
    else if (colorBy === 'is_job_application')        key = p.is_job_application === null ? 'unknown' : String(p.is_job_application);
    else                                              key = p.military_service_argument === null ? 'unknown' : String(p.military_service_argument);
    counts[key] = (counts[key] || 0) + 1;
  }

  const total  = active.length;
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  let barHtml = '<div class="ov-stat-bar">';
  for (const [key, cnt] of sorted) {
    const pct = cnt / total * 100;
    const cls = ovGetSwatchClass(colorBy, key);
    barHtml += '<div class="ov-stat-seg ' + cls + '" style="width:' + pct.toFixed(2) + '%">' +
               (pct > 5 ? pct.toFixed(1) + '%' : '') + '</div>';
  }
  barHtml += '</div>';

  let legendHtml = '<div class="ov-stat-legend">';
  for (const [key, cnt] of sorted) {
    const pct = (cnt / total * 100).toFixed(1);
    const cls = ovGetSwatchClass(colorBy, key);
    legendHtml += '<span class="ov-stat-legend-item">' +
      '<span class="ov-stat-swatch ' + cls + '"></span>' +
      '<span class="ov-stat-label">' + esc(key) + ' </span>' +
      '<span class="ov-stat-pct">' + pct + '%</span>' +
      '</span>';
  }
  legendHtml += '</div>';

  statsEl.innerHTML = barHtml + legendHtml;
}

function ovHandleMouseMove(e) {
  const cell = e.target.closest('.ov-cell');
  const tip  = document.getElementById('ov-tooltip');
  if (!cell) { tip.style.display = 'none'; return; }

  const parts = [
    cell.dataset.doc + ' / ' + cell.dataset.page,
    cell.dataset.lang ? 'Language: '  + cell.dataset.lang : null,
    cell.dataset.cat  ? 'Category: '  + cell.dataset.cat  : null,
    cell.dataset.date ? 'Date: '      + cell.dataset.date : null,
  ].filter(Boolean);

  tip.textContent = parts.join('  |  ');
  tip.style.display = 'block';
  tip.style.left = (e.clientX + 14) + 'px';
  tip.style.top  = (e.clientY - 10) + 'px';
}

function ovHandleClick(e) {
  const cell = e.target.closest('.ov-cell');
  if (!cell) return;
  ovNavigateToReview(cell.dataset.col, cell.dataset.doc, cell.dataset.page);
}

async function ovNavigateToReview(collection, doc, page) {
  // Switch tab
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  const reviewBtn = document.querySelector('[data-tab=review]');
  reviewBtn.classList.add('active');
  document.getElementById('tab-review').classList.add('active');
  document.querySelector('main').classList.add('review-active');

  if (!rvLoaded) { await rvLoadCollections(); rvLoaded = true; }

  const colSel = document.getElementById('rv-collection');
  colSel.value = collection;
  await rvSelectCollection(collection);

  const docSel = document.getElementById('rv-document');
  docSel.value = doc;
  await rvSelectDocument(doc);

  const idx = rvPages.findIndex(p => p.stem === page);
  if (idx >= 0) rvLoadPage(idx);
}

// Event wiring
document.getElementById('ov-collection').addEventListener('change', () => {
  if (ovPages.length) { ovRender(); }
});
document.getElementById('ov-color-by').addEventListener('change', () => {
  if (ovPages.length) { ovRender(); }
});
['ov-filter-language', 'ov-filter-category', 'ov-filter-job', 'ov-filter-mil'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => { ovApplyFilter(); });
});
document.getElementById('ov-refresh').addEventListener('click', () => {
  ovLoaded = false;
  ovLoad();
  ovLoaded = true;
});
