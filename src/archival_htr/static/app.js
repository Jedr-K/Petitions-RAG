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
function switchTab(name) {
  const valid = ['search', 'ask', 'query', 'ingest', 'overview', 'review'];
  if (!valid.includes(name)) name = 'search';

  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  const btn = document.querySelector(`[data-tab="${name}"]`);
  btn.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');

  const isReview   = name === 'review';
  const isOverview = name === 'overview';
  document.querySelector('main').classList.toggle('review-active', isReview || isOverview);

  if (isReview   && !rvLoaded) { rvLoadCollections(); rvLoaded = true; }
  if (isOverview && !ovLoaded) { ovLoad(); ovLoaded = true; }
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const name = btn.dataset.tab;
    if (window.location.hash !== '#' + name) {
      history.pushState(null, '', '#' + name);
    }
    switchTab(name);
  });
});

window.addEventListener('hashchange', () => {
  switchTab(window.location.hash.slice(1));
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
let rvCurrentMeta = null;
let rvZoom = 1.0;
let rvZoomMin = 0.1;
let rvImgNaturalW = 0;
let rvImgNaturalH = 0;
const RV_ZOOM_STEP = 0.1;
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
  wrap.style.marginLeft = Math.max(0, (panel.clientWidth  - vw) / 2) + 'px';
  wrap.style.marginTop  = Math.max(0, (panel.clientHeight - vh) / 2) + 'px';
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
  rvCurrentMeta = null;
  document.getElementById('rv-meta-strip').className = 'hidden';
  document.getElementById('rv-meta-edit-form').classList.remove('open');
  document.getElementById('rv-meta-save-status').textContent = '';
}

function rvRenderMeta(meta) {
  const strip = document.getElementById('rv-meta-strip');
  rvCurrentMeta = meta;
  document.getElementById('rv-meta-edit-form').classList.remove('open');
  document.getElementById('rv-meta-save-status').textContent = '';
  if (!meta) {
    // No metadata on file yet — show a minimal strip with an "Add" button
    ['rv-edit-language','rv-edit-date','rv-edit-relation','rv-edit-job-type',
     'rv-edit-petitioner-name','rv-edit-occupation','rv-edit-residence',
     'rv-edit-birthplace','rv-edit-age','rv-edit-writing-for'].forEach(id => {
      document.getElementById(id).value = '';
    });
    ['rv-edit-category','rv-edit-scope','rv-edit-job','rv-edit-mil',
     'rv-edit-construction','rv-edit-belgian','rv-edit-gender','rv-edit-petition-type'].forEach(id => {
      document.getElementById(id).value = '';
    });
    strip.innerHTML = '<button id="rv-meta-edit-btn" style="margin-left:0">&#9998; Add metadata</button>';
    strip.className = '';
    document.getElementById('rv-meta-edit-btn').addEventListener('click', () => {
      document.getElementById('rv-meta-edit-form').classList.add('open');
    });
    return;
  }

  // Populate edit form inputs with current values
  document.getElementById('rv-edit-language').value = meta.language || '';
  document.getElementById('rv-edit-category').value = meta.category || '';
  document.getElementById('rv-edit-date').value = meta.date_submission_writing || '';
  document.getElementById('rv-edit-scope').value = meta.single_page_or_part || '';
  document.getElementById('rv-edit-relation').value = meta.related_to_others || '';
  document.getElementById('rv-edit-job').value =
    meta.is_job_application === null || meta.is_job_application === undefined
      ? '' : String(meta.is_job_application);
  document.getElementById('rv-edit-job-type').value = meta.job_application_type || '';
  document.getElementById('rv-edit-mil').value =
    meta.military_service_argument === null || meta.military_service_argument === undefined
      ? '' : String(meta.military_service_argument);
  document.getElementById('rv-edit-construction').value =
    meta.construction_works === null || meta.construction_works === undefined
      ? '' : String(meta.construction_works);
  document.getElementById('rv-edit-belgian').value =
    meta.belgian_revolution_1830 === null || meta.belgian_revolution_1830 === undefined
      ? '' : String(meta.belgian_revolution_1830);
  document.getElementById('rv-edit-petitioner-name').value = meta.petitioner_name || '';
  document.getElementById('rv-edit-gender').value          = meta.petitioner_gender || '';
  document.getElementById('rv-edit-occupation').value      = meta.petitioner_occupation || '';
  document.getElementById('rv-edit-residence').value       = meta.petitioner_residence || '';
  document.getElementById('rv-edit-birthplace').value      = meta.petitioner_birthplace || '';
  document.getElementById('rv-edit-age').value             = meta.petitioner_age || '';
  document.getElementById('rv-edit-writing-for').value     = meta.petitioner_writing_for || '';
  document.getElementById('rv-edit-petition-type').value   = meta.petition_type || '';

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
  if (meta.petitioner_name)         html += item('Name',     txt(meta.petitioner_name));
  if (meta.petitioner_gender && meta.petitioner_gender !== 'unknown')
    html += item('Gender', txt(meta.petitioner_gender));
  if (meta.petitioner_occupation)   html += item('Occupation',  txt(meta.petitioner_occupation));
  if (meta.petitioner_residence)    html += item('Residence',   txt(meta.petitioner_residence));
  if (meta.petitioner_birthplace)   html += item('Birthplace',  txt(meta.petitioner_birthplace));
  if (meta.petitioner_age)          html += item('Age',         txt(meta.petitioner_age));
  if (meta.petitioner_writing_for)  html += item('Writing for', txt(meta.petitioner_writing_for));
  if (meta.petition_type && meta.petition_type !== 'other')
    html += item('Petition type', txt(meta.petition_type));
  html += bool('Job application',    meta.is_job_application);
  if (meta.is_job_application && meta.job_application_type)
    html += item('Job type', txt(meta.job_application_type));
  html += bool('Military service',   meta.military_service_argument);
  html += bool('Construction works', meta.construction_works);
  html += bool('Belgian Rev. 1830',  meta.belgian_revolution_1830);

  html += '<button id="rv-meta-edit-btn" title="Edit metadata">&#9998;</button>';
  strip.innerHTML = html || '<span class="rv-meta-none">No metadata — <button id="rv-meta-edit-btn" title="Add metadata">&#9998; Add</button></span>';
  strip.className = '';

  document.getElementById('rv-meta-edit-btn').addEventListener('click', () => {
    document.getElementById('rv-meta-edit-form').classList.add('open');
  });
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

document.getElementById('rv-meta-cancel-btn').addEventListener('click', () => {
  document.getElementById('rv-meta-edit-form').classList.remove('open');
  document.getElementById('rv-meta-save-status').textContent = '';
  // Reset form inputs back to the last loaded metadata
  if (rvCurrentMeta) rvRenderMeta(rvCurrentMeta);
});

document.getElementById('rv-edit-scope').addEventListener('change', function () {
  const rel = document.getElementById('rv-edit-relation');
  if (this.value === 'single document') {
    rel.value = 'standalone';
  } else if (this.value === 'part of dossier' && (!rel.value || rel.value === 'standalone')) {
    rel.value = 'attached to ';
  }
});

document.getElementById('rv-meta-save-btn').addEventListener('click', async () => {
  if (!rvCurrentCollection || !rvCurrentDocument || rvPages.length === 0) return;
  const stem = rvPages[rvPageIdx].stem;
  const base = '/api/review/' + encodeURIComponent(rvCurrentCollection) +
               '/' + encodeURIComponent(rvCurrentDocument) +
               '/' + encodeURIComponent(stem);
  const statusEl = document.getElementById('rv-meta-save-status');
  statusEl.style.color = 'var(--text-dim)';
  statusEl.textContent = 'Saving…';

  const jobVal     = document.getElementById('rv-edit-job').value;
  const milVal     = document.getElementById('rv-edit-mil').value;
  const belgianVal = document.getElementById('rv-edit-belgian').value;
  const body = {
    language:                  document.getElementById('rv-edit-language').value.trim() || null,
    category:                  document.getElementById('rv-edit-category').value || null,
    date_submission_writing:   document.getElementById('rv-edit-date').value.trim() || null,
    single_page_or_part:       document.getElementById('rv-edit-scope').value || null,
    related_to_others:         document.getElementById('rv-edit-relation').value.trim() || null,
    is_job_application:        jobVal === '' ? null : jobVal === 'true',
    job_application_type:      document.getElementById('rv-edit-job-type').value.trim() || null,
    military_service_argument: milVal === '' ? null : milVal === 'true',
    construction_works:        (() => { const v = document.getElementById('rv-edit-construction').value; return v === '' ? null : v === 'true'; })(),
    belgian_revolution_1830:   belgianVal === '' ? null : belgianVal === 'true',
    petitioner_name:           document.getElementById('rv-edit-petitioner-name').value.trim() || null,
    petitioner_gender:         document.getElementById('rv-edit-gender').value || null,
    petitioner_occupation:     document.getElementById('rv-edit-occupation').value.trim() || null,
    petitioner_residence:      document.getElementById('rv-edit-residence').value.trim() || null,
    petitioner_birthplace:     document.getElementById('rv-edit-birthplace').value.trim() || null,
    petitioner_age:            document.getElementById('rv-edit-age').value.trim() || null,
    petitioner_writing_for:    document.getElementById('rv-edit-writing-for').value.trim() || null,
    petition_type:             document.getElementById('rv-edit-petition-type').value || null,
  };
  try {
    const resp = await fetch(base + '/metadata', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      const saved = await resp.json();
      statusEl.style.color = 'var(--success)';
      statusEl.textContent = 'Saved ✓';
      setTimeout(() => { statusEl.textContent = ''; }, 2000);
      rvRenderMeta(saved);
    } else {
      const d = await resp.json().catch(() => ({}));
      statusEl.style.color = 'var(--accent)';
      statusEl.textContent = d.detail || 'Save failed';
    }
  } catch (err) {
    statusEl.style.color = 'var(--accent)';
    statusEl.textContent = 'Error: ' + err.message;
  }
});

// ── Overview ──────────────────────────────────────────────────────────────────
let ovLoaded    = false;
let ovPages     = [];
let ovSortByDate = false;
let ovSampleDocs   = null;   // null until first fetch; {col: [docIds]} after
let ovSampleActive = false;

function parseSortableDate(str) {
  if (!str) return Infinity;
  const m = str.match(/\b([0-9]{3,4})\b/);
  return m ? parseInt(m[1], 10) : Infinity;
}

const OV_LANG_CLASS = {
  dutch:   'ov-lang-dutch',
  french:  'ov-lang-french',
  latin:   'ov-lang-latin',
  english: 'ov-lang-english',
  german:  'ov-lang-german',
  unknown: 'ov-lang-unknown',
};

const OV_CAT_CLASS = {
  'petition':    'ov-cat-petitie',
  'apostille':   'ov-cat-appostille',
  'attachement': 'ov-cat-bijlage',
  'other':       'ov-cat-andere',
};

const OV_PT_CLASS = {
  'request for financial aid':  'ov-lang-dutch',
  'request for permission':     'ov-lang-french',
  'request for certification':  'ov-lang-latin',
  'job application':            'ov-lang-english',
  'complaint':                  'ov-lang-german',
  'other':                      'ov-lang-other',
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
  if (colorBy === 'petitioner_gender') {
    const g = (page.petitioner_gender || 'unknown').toLowerCase();
    if (g === 'male')   return 'ov-gender-male';
    if (g === 'female') return 'ov-gender-female';
    return 'ov-bool-unknown';
  }
  if (colorBy === 'petition_type') {
    const k = (page.petition_type || 'other').toLowerCase();
    return OV_PT_CLASS[k] || 'ov-lang-other';
  }
  // boolean fields
  const boolVal = colorBy === 'is_job_application'        ? page.is_job_application
                : colorBy === 'military_service_argument' ? page.military_service_argument
                : colorBy === 'construction_works'        ? page.construction_works
                : colorBy === 'belgian_revolution_1830'   ? page.belgian_revolution_1830
                : null;
  if (boolVal === true)  return 'ov-bool-true';
  if (boolVal === false) return 'ov-bool-false';
  return 'ov-bool-unknown';
}

function ovGetSwatchClass(colorBy, key) {
  if (colorBy === 'language') return OV_LANG_CLASS[key.toLowerCase()] || 'ov-lang-other';
  if (colorBy === 'category') return OV_CAT_CLASS[key.toLowerCase()]  || 'ov-cat-unknown';
  if (colorBy === 'petitioner_gender') {
    if (key.toLowerCase() === 'male')   return 'ov-gender-male';
    if (key.toLowerCase() === 'female') return 'ov-gender-female';
    return 'ov-bool-unknown';
  }
  if (colorBy === 'petition_type') {
    return OV_PT_CLASS[key.toLowerCase()] || 'ov-lang-other';
  }
  if (key === 'true')  return 'ov-bool-true';
  if (key === 'false') return 'ov-bool-false';
  return 'ov-bool-unknown';
}

function ovContains(field, q) {
  return q ? (field || '').toLowerCase().includes(q.toLowerCase()) : true;
}
function ovMatchesFilter(page) {
  const fl   = document.getElementById('ov-filter-language').value;
  const fc   = document.getElementById('ov-filter-category').value;
  const fsc  = document.getElementById('ov-filter-scope').value;
  const fj   = document.getElementById('ov-filter-job').value;
  const fjt  = document.getElementById('ov-filter-job-type').value;
  const fm   = document.getElementById('ov-filter-mil').value;
  const fco  = document.getElementById('ov-filter-construction').value;
  const fb   = document.getElementById('ov-filter-belgian').value;
  const fg   = document.getElementById('ov-filter-gender').value;
  const fpt  = document.getElementById('ov-filter-petition-type').value;
  if (fl  && (page.language            || '').toLowerCase() !== fl.toLowerCase())  return false;
  if (fc  && (page.category            || '').toLowerCase() !== fc.toLowerCase())  return false;
  if (fsc && (page.single_page_or_part || '').toLowerCase() !== fsc.toLowerCase()) return false;
  if (fj  && page.is_job_application        !== (fj  === 'true')) return false;
  if (fm  && page.military_service_argument !== (fm  === 'true')) return false;
  if (fco && page.construction_works        !== (fco === 'true')) return false;
  if (fb  && page.belgian_revolution_1830   !== (fb  === 'true')) return false;
  if (!ovContains(page.job_application_type,  fjt)) return false;
  if (fg  && (page.petitioner_gender || '').toLowerCase() !== fg.toLowerCase()) return false;
  if (fpt && (page.petition_type || '').toLowerCase() !== fpt.toLowerCase()) return false;
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

  // Group pages by document; render each doc as its own card.
  const byDoc = {};
  for (const p of visible) {
    if (!byDoc[p.source_doc]) byDoc[p.source_doc] = [];
    byDoc[p.source_doc].push(p);
  }
  let docNames = Object.keys(byDoc).sort();
  if (ovSortByDate) {
    docNames.sort((a, b) => {
      const aDate = Math.min(...byDoc[a].map(p => parseSortableDate(p.date_submission_writing)));
      const bDate = Math.min(...byDoc[b].map(p => parseSortableDate(p.date_submission_writing)));
      return aDate - bDate;
    });
  }

  let html = '<div class="ov-cells">';
  for (const docName of docNames) {
    const pages = [...byDoc[docName]].sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
    html += '<div class="ov-doc-group" data-col="' + esc(pages[0].collection) + '" data-doc="' + esc(docName) + '">';
    html += '<div class="ov-doc-tag" title="' + esc(docName) + '">' + esc(docName) + '</div>';
    html += '<div class="ov-doc-cells">';
    for (const p of pages) {
      const cls = ovCellClass(p, colorBy);
      const bStr = v => v === null || v === undefined ? '' : String(v);
      html += '<div class="ov-cell ' + cls + '"' +
              ' data-col="'          + esc(p.collection)  + '"' +
              ' data-doc="'          + esc(p.source_doc)  + '"' +
              ' data-page="'         + esc(p.source_page) + '"' +
              ' data-lang="'         + esc(p.language  || '') + '"' +
              ' data-cat="'          + esc(p.category  || '') + '"' +
              ' data-date="'         + esc(p.date_submission_writing || '') + '"' +
              ' data-gender="'       + esc(p.petitioner_gender || '') + '"' +
              ' data-petition-type="'+ esc(p.petition_type || '') + '"' +
              ' data-job="'          + bStr(p.is_job_application)        + '"' +
              ' data-mil="'          + bStr(p.military_service_argument) + '"' +
              ' data-construction="' + bStr(p.construction_works)        + '"' +
              '></div>';
    }
    html += '</div></div>';
  }
  html += '</div>';
  grid.innerHTML = html;

  // Event delegation — re-attach each render
  grid.addEventListener('mousemove',  ovHandleMouseMove);
  grid.addEventListener('mouseleave', () => { document.getElementById('ov-tooltip').style.display = 'none'; });
  grid.addEventListener('click',      ovHandleClick);

  ovApplyFilter();
  if (ovSampleActive) ovApplySample();
}

async function ovLoadSample() {
  if (ovSampleDocs !== null) return;
  const data = await fetch('/api/sample').then(r => r.json());
  ovSampleDocs = data.samples;
}

function ovApplySample() {
  document.querySelectorAll('.ov-doc-group').forEach(group => {
    if (!ovSampleActive || !ovSampleDocs) {
      group.classList.remove('sampled');
      return;
    }
    const col = group.dataset.col;
    const doc = group.dataset.doc;
    group.classList.toggle('sampled', (ovSampleDocs[col] || []).includes(doc));
  });
  document.getElementById('ov-highlight-sample').classList.toggle('active', ovSampleActive);
}

document.getElementById('ov-highlight-sample').addEventListener('click', async () => {
  await ovLoadSample();
  ovSampleActive = !ovSampleActive;
  ovApplySample();
});

function ovApplyFilter() {
  const anyFilter = (
    document.getElementById('ov-filter-language').value ||
    document.getElementById('ov-filter-category').value ||
    document.getElementById('ov-filter-scope').value ||
    document.getElementById('ov-filter-job').value ||
    document.getElementById('ov-filter-job-type').value ||
    document.getElementById('ov-filter-mil').value ||
    document.getElementById('ov-filter-construction').value ||
    document.getElementById('ov-filter-belgian').value ||
    document.getElementById('ov-filter-gender').value ||
    document.getElementById('ov-filter-petition-type').value
  );

  document.querySelectorAll('.ov-cell').forEach(cell => {
    if (!anyFilter) {
      cell.classList.remove('dimmed', 'highlighted');
      return;
    }
    const b = v => v === 'true' ? true : v === 'false' ? false : null;
    const pseudo = {
      language:                  cell.dataset.lang         || null,
      category:                  cell.dataset.cat          || null,
      petitioner_gender:         cell.dataset.gender       || null,
      petition_type:             cell.dataset.petitionType || null,
      is_job_application:        b(cell.dataset.job),
      military_service_argument: b(cell.dataset.mil),
      construction_works:        b(cell.dataset.construction),
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

function ovPageKey(p, colorBy) {
  if      (colorBy === 'language')                  return p.language          || 'unknown';
  else if (colorBy === 'category')                  return p.category          || 'unknown';
  else if (colorBy === 'petitioner_gender')         return p.petitioner_gender || 'unknown';
  else if (colorBy === 'petition_type')             return p.petition_type     || 'other';
  else if (colorBy === 'is_job_application')        return p.is_job_application        == null ? 'unknown' : String(p.is_job_application);
  else if (colorBy === 'military_service_argument') return p.military_service_argument == null ? 'unknown' : String(p.military_service_argument);
  else if (colorBy === 'construction_works')        return p.construction_works        == null ? 'unknown' : String(p.construction_works);
  else                                              return 'unknown';
}

function ovBuildStatSection(label, total, visibleTotal, sorted, colorBy) {
  const filtered = total !== visibleTotal;
  const noun = label === 'Pages' ? 'page' : 'document';
  let html = '<div class="ov-stat-section">';
  html += '<div class="ov-stat-section-header">' + label + '</div>';
  html += '<div class="ov-stat-total"><b>' + total + '</b> ' + noun +
    (total === 1 ? '' : 's') +
    (filtered ? ' <span style="opacity:0.7">(of ' + visibleTotal + ')</span>' : '') +
    '</div>';

  html += '<div class="ov-stat-bar">';
  for (const [key, cnt] of sorted) {
    const pct = cnt / total * 100;
    const cls = ovGetSwatchClass(colorBy, key);
    html += '<div class="ov-stat-seg ' + cls + '" style="width:' + pct.toFixed(2) + '%">' +
            (pct > 8 ? pct.toFixed(0) + '%' : '') + '</div>';
  }
  html += '</div>';

  html += '<div class="ov-stat-legend">';
  for (const [key, cnt] of sorted) {
    const pct = (cnt / total * 100).toFixed(1);
    const cls = ovGetSwatchClass(colorBy, key);
    html += '<div class="ov-stat-legend-item">' +
      '<span class="ov-stat-swatch ' + cls + '"></span>' +
      '<span class="ov-stat-label" title="' + esc(key) + '">' + esc(key) + '</span>' +
      '<span class="ov-stat-count">' + cnt + '</span>' +
      '<span class="ov-stat-pct">' + pct + '%</span>' +
      '</div>';
  }
  html += '</div></div>';
  return html;
}

function ovUpdateStats() {
  const statsEl = document.getElementById('ov-stats');
  const colorBy = document.getElementById('ov-color-by').value;
  const visible = ovVisiblePages();
  const active  = visible.filter(ovMatchesFilter);

  if (visible.length === 0) {
    statsEl.innerHTML = '<span class="rv-meta-none">No pages.</span>';
    return;
  }

  // Page-level counts
  const pageCounts = {};
  for (const p of active) {
    const key = ovPageKey(p, colorBy);
    pageCounts[key] = (pageCounts[key] || 0) + 1;
  }
  const pageSorted = Object.entries(pageCounts).sort((a, b) => b[1] - a[1]);

  // Document-level counts — assign each doc its majority key across active pages
  const docPagesByKey = {};
  for (const p of active) {
    const doc = p.source_doc;
    const key = ovPageKey(p, colorBy);
    if (!docPagesByKey[doc]) docPagesByKey[doc] = {};
    docPagesByKey[doc][key] = (docPagesByKey[doc][key] || 0) + 1;
  }
  const docCounts = {};
  for (const [, keyCounts] of Object.entries(docPagesByKey)) {
    const majority = Object.entries(keyCounts).sort((a, b) => b[1] - a[1])[0][0];
    docCounts[majority] = (docCounts[majority] || 0) + 1;
  }
  const docSorted = Object.entries(docCounts).sort((a, b) => b[1] - a[1]);

  // Visible totals (unfiltered) for "(of N)" labels
  const visibleDocs = new Set(visible.map(p => p.source_doc)).size;
  const activeDocs  = Object.keys(docPagesByKey).length;

  statsEl.innerHTML =
    ovBuildStatSection('Pages',     active.length, visible.length, pageSorted, colorBy) +
    ovBuildStatSection('Documents', activeDocs,    visibleDocs,    docSorted,  colorBy);
}

function ovHandleMouseMove(e) {
  const cell = e.target.closest('.ov-cell');
  const tip  = document.getElementById('ov-tooltip');
  if (!cell) { tip.style.display = 'none'; return; }

  const parts = [
    cell.dataset.doc + ' / ' + cell.dataset.page,
    cell.dataset.lang         ? 'Lang: '          + cell.dataset.lang         : null,
    cell.dataset.cat          ? 'Category: '      + cell.dataset.cat          : null,
    cell.dataset.date         ? 'Date: '          + cell.dataset.date         : null,
    cell.dataset.gender       ? 'Gender: '        + cell.dataset.gender       : null,
    cell.dataset.petitionType ? 'Type: '          + cell.dataset.petitionType : null,
    cell.dataset.construction === 'true' ? 'Construction works' : null,
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
  history.pushState(null, '', '#review');
  switchTab('review');

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
['ov-filter-language', 'ov-filter-category', 'ov-filter-scope', 'ov-filter-job',
 'ov-filter-mil', 'ov-filter-construction', 'ov-filter-belgian',
 'ov-filter-gender', 'ov-filter-petition-type'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => { ovApplyFilter(); });
});
document.getElementById('ov-filter-job-type').addEventListener('input', () => { ovApplyFilter(); });
document.getElementById('ov-sort-date').addEventListener('click', () => {
  ovSortByDate = !ovSortByDate;
  document.getElementById('ov-sort-date').classList.toggle('active', ovSortByDate);
  if (ovPages.length) { ovRender(); }
});
document.getElementById('ov-refresh').addEventListener('click', () => {
  ovLoaded = false;
  ovLoad();
  ovLoaded = true;
});

// ── Initial tab from URL hash ──────────────────────────────────────────────────
switchTab(window.location.hash.slice(1) || 'search');
