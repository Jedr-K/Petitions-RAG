"""FastAPI web server for archival-htr query interface."""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from archival_htr import config

app = FastAPI(title="archival-htr", description="HTR corpus query interface")


# ── Pydantic models ──────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    n: int = Field(default=5, ge=1, le=100)
    source: Optional[str] = None
    job_application: Optional[bool] = None
    military_service: Optional[bool] = None


class AskRequest(BaseModel):
    question: str
    n: int = Field(default=8, ge=1, le=50)
    source: Optional[str] = None
    job_application: Optional[bool] = None
    military_service: Optional[bool] = None


class QueryRequest(BaseModel):
    question: str
    source: str


class SearchResult(BaseModel):
    source: str
    score: float
    text: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    count: int


class AskResponse(BaseModel):
    answer: str
    sources: list[str]
    chunks_used: int


class QueryResponse(BaseModel):
    answer: str
    source: str
    context_chars: int


class SourcesResponse(BaseModel):
    sources: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_transcript(source: str) -> tuple[str, int]:
    """Return (text, char_count) for a source ID. Raises 404 if not found."""
    out = Path(config.DATA_OUTPUT_DIR)
    transcribed_dir = out / "transcribed"
    combined = transcribed_dir / f"{source}.txt"
    if combined.exists():
        text = combined.read_text(encoding="utf-8")
        return text, len(text)
    subdir = transcribed_dir / source
    if subdir.is_dir():
        parts = sorted(subdir.glob("*.txt"))
        if parts:
            text = "\n\n".join(f.read_text(encoding="utf-8") for f in parts)
            return text, len(text)
    raise HTTPException(status_code=404, detail=f"No transcript found for source '{source}'")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/sources", response_model=SourcesResponse)
def list_sources():
    """Return sorted list of available source document IDs."""
    transcribed_dir = Path(config.DATA_OUTPUT_DIR) / "transcribed"
    if not transcribed_dir.is_dir():
        return SourcesResponse(sources=[])
    sources = sorted(
        {p.stem for p in transcribed_dir.glob("*.txt")}
        | {p.name for p in transcribed_dir.iterdir() if p.is_dir()}
    )
    return SourcesResponse(sources=sources)


@app.post("/api/search", response_model=SearchResponse)
def api_search(req: SearchRequest):
    from archival_htr.rag import search as rag_search
    try:
        results = rag_search(
            req.query,
            n_results=req.n,
            source=req.source,
            job_application=req.job_application,
            military_service=req.military_service,
        )
    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("no documents", "collection", "index", "not enough")):
            return SearchResponse(results=[], count=0)
        raise HTTPException(status_code=500, detail=str(e))
    return SearchResponse(
        results=[SearchResult(source=r["source"], score=round(r["score"], 4), text=r["text"]) for r in results],
        count=len(results),
    )


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):
    from archival_htr.rag import search as rag_search
    from archival_htr.llm_client import query_with_context
    try:
        chunks = rag_search(
            req.question,
            n_results=req.n,
            source=req.source,
            job_application=req.job_application,
            military_service=req.military_service,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not chunks:
        raise HTTPException(status_code=404, detail="No relevant passages found in the corpus.")
    context_parts = [f"[Source: {r['source']}]\n{r['text']}" for r in chunks]
    context = "\n\n---\n\n".join(context_parts)
    sources_used = sorted({r["source"] for r in chunks})
    try:
        answer = query_with_context(context, req.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return AskResponse(answer=answer, sources=sources_used, chunks_used=len(chunks))


@app.post("/api/query", response_model=QueryResponse)
def api_query(req: QueryRequest):
    from archival_htr.llm_client import query_with_context
    context, context_chars = _load_transcript(req.source)
    try:
        answer = query_with_context(context, req.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return QueryResponse(answer=answer, source=req.source, context_chars=context_chars)


# ── Web UI ───────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>archival-htr</title>
<style>
  :root {
    --bg: #1a1a2e;
    --surface: #16213e;
    --surface2: #0f3460;
    --accent: #e94560;
    --accent2: #533483;
    --text: #eaeaea;
    --text-dim: #8892a4;
    --border: #2a3a5a;
    --radius: 8px;
    --success: #4caf50;
    --warn: #ff9800;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 1rem 2rem; display: flex; align-items: center; gap: 1rem; }
  header h1 { font-size: 1.25rem; font-weight: 600; color: var(--accent); letter-spacing: 0.02em; }
  header span { color: var(--text-dim); font-size: 0.85rem; }
  .tabs { display: flex; gap: 0; background: var(--surface); border-bottom: 1px solid var(--border); padding: 0 2rem; }
  .tab-btn { background: none; border: none; color: var(--text-dim); padding: 0.75rem 1.25rem; cursor: pointer; font-size: 0.9rem; border-bottom: 2px solid transparent; transition: color 0.15s, border-color 0.15s; }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  main { max-width: 900px; margin: 0 auto; padding: 2rem; }
  .form-row { margin-bottom: 1rem; }
  label { display: block; font-size: 0.8rem; color: var(--text-dim); margin-bottom: 0.35rem; text-transform: uppercase; letter-spacing: 0.05em; }
  textarea, input[type=text], select {
    width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 0.6rem 0.8rem; border-radius: var(--radius); font-size: 0.95rem; font-family: inherit;
    transition: border-color 0.15s;
  }
  textarea:focus, input[type=text]:focus, select:focus { outline: none; border-color: var(--accent2); }
  textarea { resize: vertical; min-height: 80px; }
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .form-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; }
  .slider-row { display: flex; align-items: center; gap: 0.75rem; }
  input[type=range] { flex: 1; accent-color: var(--accent); }
  .slider-val { min-width: 2rem; text-align: right; font-weight: 600; color: var(--accent); }
  button[type=submit] {
    background: var(--accent); color: #fff; border: none; padding: 0.65rem 1.5rem;
    border-radius: var(--radius); font-size: 0.95rem; cursor: pointer; font-weight: 600;
    transition: opacity 0.15s;
  }
  button[type=submit]:hover { opacity: 0.85; }
  button[type=submit]:disabled { opacity: 0.4; cursor: not-allowed; }
  .results { margin-top: 1.5rem; }
  .spinner { display: flex; justify-content: center; padding: 2rem; }
  .spinner::after {
    content: ''; width: 2rem; height: 2rem; border: 3px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { background: #2d1010; border: 1px solid var(--accent); color: #ff8080; padding: 0.8rem 1rem; border-radius: var(--radius); font-size: 0.9rem; }
  .empty { color: var(--text-dim); text-align: center; padding: 2rem; font-size: 0.9rem; }
  .result-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; margin-bottom: 0.75rem; }
  .result-card:hover { border-color: var(--accent2); }
  .card-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.6rem; }
  .badge { background: var(--accent2); color: #fff; font-size: 0.75rem; padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 600; }
  .score-track { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .score-bar { height: 100%; background: linear-gradient(90deg, var(--accent2), var(--accent)); border-radius: 3px; transition: width 0.3s; }
  .score-label { font-size: 0.75rem; color: var(--text-dim); min-width: 3rem; text-align: right; }
  .excerpt { font-size: 0.875rem; color: var(--text-dim); line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .answer-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem; }
  .answer-meta { font-size: 0.8rem; color: var(--text-dim); margin-bottom: 0.75rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .meta-tag { background: var(--surface2); padding: 0.2rem 0.5rem; border-radius: 4px; }
  .answer-text { font-size: 0.925rem; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
  select option[value=""] { color: var(--text-dim); }
</style>
</head>
<body>
<header>
  <h1>archival-htr</h1>
  <span>Historical manuscript corpus query interface</span>
</header>

<div class="tabs">
  <button class="tab-btn active" data-tab="search">Search</button>
  <button class="tab-btn" data-tab="ask">Ask</button>
  <button class="tab-btn" data-tab="query">Query</button>
</div>

<main>

  <!-- SEARCH TAB -->
  <div id="tab-search" class="tab-panel active">
    <form id="search-form">
      <div class="form-row">
        <label for="s-query">Search query</label>
        <textarea id="s-query" placeholder="e.g. petition requesting military pension after service in Flanders" required></textarea>
      </div>
      <div class="form-grid-3">
        <div class="form-row">
          <label>Results <span class="slider-val" id="s-n-val">5</span></label>
          <div class="slider-row">
            <input type="range" id="s-n" min="1" max="20" value="5">
          </div>
        </div>
        <div class="form-row">
          <label for="s-source">Source filter (optional)</label>
          <input type="text" id="s-source" placeholder="e.g. 14">
        </div>
        <div class="form-row" style="grid-column: span 1"><!-- spacer --></div>
      </div>
      <div class="form-grid">
        <div class="form-row">
          <label for="s-job">Job application</label>
          <select id="s-job">
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div class="form-row">
          <label for="s-mil">Military service</label>
          <select id="s-mil">
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
      </div>
      <button type="submit">Search</button>
    </form>
    <div id="search-results" class="results"></div>
  </div>

  <!-- ASK TAB -->
  <div id="tab-ask" class="tab-panel">
    <form id="ask-form">
      <div class="form-row">
        <label for="a-question">Question</label>
        <textarea id="a-question" placeholder="e.g. What arguments do petitioners use to justify their request for a pension?" required></textarea>
      </div>
      <div class="form-grid-3">
        <div class="form-row">
          <label>Context chunks <span class="slider-val" id="a-n-val">8</span></label>
          <div class="slider-row">
            <input type="range" id="a-n" min="1" max="20" value="8">
          </div>
        </div>
        <div class="form-row">
          <label for="a-source">Source filter (optional)</label>
          <input type="text" id="a-source" placeholder="e.g. 14">
        </div>
        <div class="form-row"></div>
      </div>
      <div class="form-grid">
        <div class="form-row">
          <label for="a-job">Job application</label>
          <select id="a-job">
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div class="form-row">
          <label for="a-mil">Military service</label>
          <select id="a-mil">
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
      </div>
      <button type="submit">Ask</button>
    </form>
    <div id="ask-results" class="results"></div>
  </div>

  <!-- QUERY TAB -->
  <div id="tab-query" class="tab-panel">
    <form id="query-form">
      <div class="form-row">
        <label for="q-source">Source document</label>
        <select id="q-source" required>
          <option value="">Loading sources…</option>
        </select>
      </div>
      <div class="form-row">
        <label for="q-question">Question</label>
        <textarea id="q-question" placeholder="e.g. Who signed this petition and what was their rank?" required></textarea>
      </div>
      <button type="submit" id="q-submit" disabled>Query</button>
    </form>
    <div id="query-results" class="results"></div>
  </div>

</main>

<script>
// ── Tab switching ────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  });
});

// ── Slider labels ────────────────────────────────────────────────────────────
document.getElementById('s-n').addEventListener('input', e => {
  document.getElementById('s-n-val').textContent = e.target.value;
});
document.getElementById('a-n').addEventListener('input', e => {
  document.getElementById('a-n-val').textContent = e.target.value;
});

// ── Source list ──────────────────────────────────────────────────────────────
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
document.getElementById('tab-query') && document.querySelector('[data-tab=query]').addEventListener('click', loadSources);

// ── Helpers ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}

// ── Search ───────────────────────────────────────────────────────────────────
document.getElementById('search-form').addEventListener('submit', async e => {
  e.preventDefault();
  const resultsEl = document.getElementById('search-results');
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
    if (data.count === 0) {
      resultsEl.innerHTML = '<div class="empty">No results found.</div>';
      return;
    }
    resultsEl.innerHTML = data.results.map((r, i) => {
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
  } catch(err) {
    showError('search-results', err.message);
  }
});

// ── Ask ──────────────────────────────────────────────────────────────────────
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
  } catch(err) {
    showError('ask-results', err.message);
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
  } catch(err) {
    showError('query-results', err.message);
  }
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=_HTML)
