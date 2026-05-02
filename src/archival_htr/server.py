"""FastAPI web server for archival-htr query interface."""
import csv
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from archival_htr import config

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="archival-htr", description="HTR corpus query interface")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# ── Ingest background state ──────────────────────────────────────────────────

_ingest_lock = threading.Lock()
_ingest_running = False
_ingest_log: list[str] = []
_ingest_exit_code: Optional[int] = None
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]|\r")


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


class IngestRequest(BaseModel):
    overwrite: bool = False
    doc: list[str] = []


class IngestStatusResponse(BaseModel):
    running: bool
    log: list[str]
    exit_code: Optional[int] = None


class FinalizedRequest(BaseModel):
    text: str


class PageMetadataResponse(BaseModel):
    language: Optional[str] = None
    category: Optional[str] = None
    date_submission_writing: Optional[str] = None
    single_page_or_part: Optional[str] = None
    related_to_others: Optional[str] = None
    is_job_application: Optional[bool] = None
    military_service_argument: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

_SAFE_ID_RE = re.compile(r"^[\w\-\.]+$")
_REVIEW_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def _safe_id(s: str) -> str:
    if not _SAFE_ID_RE.match(s) or ".." in s:
        raise HTTPException(status_code=400, detail=f"Invalid identifier: {s!r}")
    return s


def _find_image(source: str, page: str) -> Path | None:
    folder = Path(config.DATA_INPUT_DIR) / source
    for ext in _REVIEW_EXTENSIONS:
        p = folder / f"{page}{ext}"
        if p.exists():
            return p
    return None


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


@app.post("/api/ingest", response_model=IngestStatusResponse)
def api_ingest(req: IngestRequest):
    global _ingest_running, _ingest_log, _ingest_exit_code
    with _ingest_lock:
        if _ingest_running:
            raise HTTPException(status_code=409, detail="Ingest is already running.")
        _ingest_running = True
        _ingest_log = []
        _ingest_exit_code = None

    def _run():
        global _ingest_running, _ingest_exit_code
        try:
            cmd = ["archival-htr", "ingest"]
            if req.overwrite:
                cmd.append("--overwrite")
            for d in req.doc:
                cmd += ["--doc", d]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={**__import__("os").environ, "NO_COLOR": "1", "TERM": "dumb"},
            )
            for line in proc.stdout:
                clean = _ANSI_RE.sub("", line).rstrip()
                if clean:
                    with _ingest_lock:
                        _ingest_log.append(clean)
            proc.wait()
            _ingest_exit_code = proc.returncode
        except Exception as exc:
            with _ingest_lock:
                _ingest_log.append(f"ERROR: {exc}")
            _ingest_exit_code = 1
        finally:
            _ingest_running = False

    threading.Thread(target=_run, daemon=True).start()
    return IngestStatusResponse(running=True, log=[], exit_code=None)


@app.get("/api/ingest/status", response_model=IngestStatusResponse)
def api_ingest_status():
    with _ingest_lock:
        return IngestStatusResponse(
            running=_ingest_running,
            log=list(_ingest_log),
            exit_code=_ingest_exit_code,
        )


# ── Review endpoints ──────────────────────────────────────────────────────────

@app.get("/api/review/{source}/pages")
def review_pages(source: str):
    """List pages for a source document with per-page availability flags."""
    _safe_id(source)
    input_folder = Path(config.DATA_INPUT_DIR) / source
    if not input_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Source '{source}' not found in input directory.")
    out = Path(config.DATA_OUTPUT_DIR)
    has_imported = (
        (out / "imported" / f"{source}.txt").exists()
        or (out / "imported" / f"{source}.xml").exists()
    )
    stems = sorted(
        p.stem for p in input_folder.iterdir()
        if p.suffix.lower() in _REVIEW_EXTENSIONS
    )
    return [
        {
            "stem": stem,
            "has_imported": has_imported,
            "has_transcribed": (out / "transcribed" / source / f"{stem}.txt").exists(),
            "has_finalized": (out / "finalized" / source / f"{stem}.txt").exists(),
        }
        for stem in stems
    ]


@app.get("/api/review/{source}/{page}/image")
def review_image(source: str, page: str):
    """Serve the original scan image for a page."""
    _safe_id(source)
    _safe_id(page)
    img = _find_image(source, page)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Image not found for {source}/{page}")
    return FileResponse(img)


@app.get("/api/review/{source}/{page}/imported", response_class=PlainTextResponse)
def review_imported(source: str, page: str):
    """Return the imported (pre-existing) transcript for this document."""
    _safe_id(source)
    _safe_id(page)
    out = Path(config.DATA_OUTPUT_DIR) / "imported"
    txt = out / f"{source}.txt"
    if txt.exists():
        return PlainTextResponse(txt.read_text(encoding="utf-8"))
    xml = out / f"{source}.xml"
    if xml.exists():
        from archival_htr.ingest import extract_text_from_page_xml
        return PlainTextResponse(extract_text_from_page_xml(xml))
    raise HTTPException(status_code=404, detail=f"No imported transcript for '{source}'")


@app.get("/api/review/{source}/{page}/transcribed", response_class=PlainTextResponse)
def review_transcribed(source: str, page: str):
    """Return the LLM-refined transcript for a specific page."""
    _safe_id(source)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "transcribed" / source / f"{page}.txt"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No LLM transcript for '{source}/{page}'")
    return PlainTextResponse(p.read_text(encoding="utf-8"))


@app.get("/api/review/{source}/{page}/finalized", response_class=PlainTextResponse)
def review_get_finalized(source: str, page: str):
    """Return the saved finalized transcript for a page."""
    _safe_id(source)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "finalized" / source / f"{page}.txt"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No finalized transcript for '{source}/{page}'")
    return PlainTextResponse(p.read_text(encoding="utf-8"))


@app.post("/api/review/{source}/{page}/finalized")
def review_save_finalized(source: str, page: str, req: FinalizedRequest):
    """Save the finalized transcript for a page."""
    _safe_id(source)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "finalized" / source / f"{page}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.text, encoding="utf-8")
    return {"saved": True}


@app.get("/api/review/{source}/{page}/metadata", response_model=PageMetadataResponse)
def review_metadata(source: str, page: str):
    """Return taxonomy metadata for a specific page from its per-page CSV."""
    _safe_id(source)
    _safe_id(page)
    csv_path = Path(config.DATA_OUTPUT_DIR) / "metadata" / f"{source}_{page}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail=f"No metadata for '{source}/{page}'")

    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Empty metadata for '{source}/{page}'")

    row = rows[0]

    def _clean(val: str | None) -> Optional[str]:
        if not val or val.strip().lower() in ("unknown", "n/a", ""):
            return None
        return val.strip()

    def _parse_bool(val: str | None) -> Optional[bool]:
        if val is None:
            return None
        v = val.strip().lower()
        if v == "true":
            return True
        if v == "false":
            return False
        return None

    return PageMetadataResponse(
        language=_clean(row.get("language")),
        category=_clean(row.get("category")),
        date_submission_writing=_clean(row.get("date_submission_writing")),
        single_page_or_part=_clean(row.get("single_page_or_part")),
        related_to_others=_clean(row.get("related_to_others")),
        is_job_application=_parse_bool(row.get("is_job_application")),
        military_service_argument=_parse_bool(row.get("military_service_argument")),
    )


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=(_STATIC_DIR / "index.html").read_text(encoding="utf-8"))
