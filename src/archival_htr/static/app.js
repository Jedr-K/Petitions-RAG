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
    const isReview = btn.dataset.tab === 'review';
    document.querySelector('main').classList.toggle('review-active', isReview);
    if (isReview && !rvLoaded) { rvLoadSources(); rvLoaded = true; }
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
let rvCurrentSource = '';

async function rvLoadSources() {
  const sel = document.getElementById('rv-source');
  try {
    const resp = await fetch('/api/sources');
    const data = await resp.json();
    if (data.sources.length === 0) {
      sel.innerHTML = '<option value="">— no sources —</option>';
    } else {
      sel.innerHTML = '<option value="">Select document…</option>' +
        data.sources.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('');
    }
  } catch {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

async function rvSelectSource(source) {
  rvCurrentSource = source;
  rvPages = [];
  rvPageIdx = 0;
  rvClearPanels();
  if (!source) { rvUpdateNav(); return; }
  try {
    const resp = await fetch('/api/review/' + encodeURIComponent(source) + '/pages');
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
  const base = '/api/review/' + encodeURIComponent(rvCurrentSource) + '/' + encodeURIComponent(stem);

  fetch(base + '/metadata')
    .then(r => r.ok ? r.json() : null)
    .then(meta => rvRenderMeta(meta))
    .catch(() => rvRenderMeta(null));

  document.getElementById('rv-image-panel').innerHTML =
    '<img src="' + base + '/image?t=' + Date.now() + '" alt="page image">';

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

document.getElementById('rv-source').addEventListener('change', e => rvSelectSource(e.target.value));
document.getElementById('rv-prev').addEventListener('click', () => {
  if (rvPageIdx > 0) rvLoadPage(rvPageIdx - 1);
});
document.getElementById('rv-next').addEventListener('click', () => {
  if (rvPageIdx < rvPages.length - 1) rvLoadPage(rvPageIdx + 1);
});

document.getElementById('rv-save').addEventListener('click', async () => {
  if (!rvCurrentSource || rvPages.length === 0) return;
  const stem = rvPages[rvPageIdx].stem;
  const base = '/api/review/' + encodeURIComponent(rvCurrentSource) + '/' + encodeURIComponent(stem);
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
