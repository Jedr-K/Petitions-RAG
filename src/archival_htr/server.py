"""FastAPI web server for archival-htr query interface."""
import csv
import re
import subprocess
import threading
from collections import Counter
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


class SampleResponse(BaseModel):
    samples: dict[str, list[str]]


class PageMetadataResponse(BaseModel):
    language: Optional[str] = None
    category: Optional[str] = None
    date_submission_writing: Optional[str] = None
    single_page_or_part: Optional[str] = None
    related_to_others: Optional[str] = None
    is_job_application: Optional[bool] = None
    job_application_type: Optional[str] = None
    military_service_argument: Optional[bool] = None
    construction_works: Optional[bool] = None
    belgian_revolution_1830: Optional[bool] = None
    petitioner_name: Optional[str] = None
    petitioner_gender: Optional[str] = None
    petitioner_occupation: Optional[str] = None
    petitioner_residence: Optional[str] = None
    petitioner_birthplace: Optional[str] = None
    petitioner_age: Optional[str] = None
    petitioner_writing_for: Optional[str] = None
    petition_type: Optional[str] = None


class ApplyMetadataResponse(BaseModel):
    updated: int


class OverviewPage(BaseModel):
    collection: str
    source_doc: str
    source_page: str
    language: Optional[str] = None
    single_page_or_part: Optional[str] = None
    related_to_others: Optional[str] = None
    date_submission_writing: Optional[str] = None
    category: Optional[str] = None
    is_job_application: Optional[bool] = None
    job_application_type: Optional[str] = None
    military_service_argument: Optional[bool] = None
    construction_works: Optional[bool] = None
    belgian_revolution_1830: Optional[bool] = None
    petitioner_name: Optional[str] = None
    petitioner_gender: Optional[str] = None
    petitioner_occupation: Optional[str] = None
    petitioner_residence: Optional[str] = None
    petitioner_birthplace: Optional[str] = None
    petitioner_age: Optional[str] = None
    petitioner_writing_for: Optional[str] = None
    petition_type: Optional[str] = None


class OverviewStats(BaseModel):
    total: int
    by_language: dict[str, int]
    by_category: dict[str, int]
    by_is_job_application: dict[str, int]
    by_military_service: dict[str, int]


class OverviewResponse(BaseModel):
    pages: list[OverviewPage]
    stats: OverviewStats


# ── Helpers ──────────────────────────────────────────────────────────────────

_SAFE_ID_RE = re.compile(r"^[\w\-\. ]+$")
_REVIEW_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def _safe_id(s: str) -> str:
    if not _SAFE_ID_RE.match(s) or ".." in s:
        raise HTTPException(status_code=400, detail=f"Invalid identifier: {s!r}")
    return s


def _find_image(collection: str, document: str, page: str) -> Path | None:
    folder = Path(config.DATA_INPUT_DIR) / collection / document
    for ext in _REVIEW_EXTENSIONS:
        p = folder / f"{page}{ext}"
        if p.exists():
            return p
    return None


def _parse_overview_row(row: dict, collection: str) -> Optional[OverviewPage]:
    source_doc  = row.get("source_doc",  "").strip()
    source_page = row.get("source_page", "").strip()
    if not source_doc or not source_page:
        return None

    def _clean(v: str | None) -> Optional[str]:
        s = (v or "").strip()
        return s if s and s.lower() not in ("unknown", "n/a", "") else None

    def _parse_bool(v: str | None) -> Optional[bool]:
        s = (v or "").strip().lower()
        if s == "true":  return True
        if s == "false": return False
        return None

    return OverviewPage(
        collection=collection,
        source_doc=source_doc,
        source_page=source_page,
        language=_clean(row.get("language")),
        single_page_or_part=_clean(row.get("single_page_or_part")),
        related_to_others=_clean(row.get("related_to_others")),
        date_submission_writing=_clean(row.get("date_submission_writing")),
        category=_clean(row.get("category")),
        is_job_application=_parse_bool(row.get("is_job_application")),
        job_application_type=_clean(row.get("job_application_type")),
        military_service_argument=_parse_bool(row.get("military_service_argument")),
        construction_works=_parse_bool(row.get("construction_works")),
        belgian_revolution_1830=_parse_bool(row.get("belgian_revolution_1830")),
        petitioner_name=_clean(row.get("petitioner_name")),
        petitioner_gender=_clean(row.get("petitioner_gender")),
        petitioner_occupation=_clean(row.get("petitioner_occupation")),
        petitioner_residence=_clean(row.get("petitioner_residence")),
        petitioner_birthplace=_clean(row.get("petitioner_birthplace")),
        petitioner_age=_clean(row.get("petitioner_age")),
        petitioner_writing_for=_clean(row.get("petitioner_writing_for")),
        petition_type=_clean(row.get("petition_type")),
    )


def _compute_overview_stats(pages: list[OverviewPage]) -> OverviewStats:
    lang_ctr = Counter()
    cat_ctr  = Counter()
    job_ctr  = Counter()
    mil_ctr  = Counter()
    for p in pages:
        lang_ctr[p.language or "unknown"] += 1
        cat_ctr[p.category or "unknown"] += 1
        job_ctr["true" if p.is_job_application is True else
                "false" if p.is_job_application is False else "unknown"] += 1
        mil_ctr["true" if p.military_service_argument is True else
                "false" if p.military_service_argument is False else "unknown"] += 1
    return OverviewStats(
        total=len(pages),
        by_language=dict(lang_ctr),
        by_category=dict(cat_ctr),
        by_is_job_application=dict(job_ctr),
        by_military_service=dict(mil_ctr),
    )


def _load_transcript(source: str) -> tuple[str, int]:
    """Return (text, char_count) for a 'collection/document' source ID. Raises 404 if not found."""
    collection, _, document = source.partition("/")
    out = Path(config.DATA_OUTPUT_DIR)
    transcribed_dir = out / "transcribed"
    combined = transcribed_dir / collection / f"{document}.txt"
    if combined.exists():
        text = combined.read_text(encoding="utf-8")
        return text, len(text)
    subdir = transcribed_dir / collection / document
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


@app.get("/api/sample", response_model=SampleResponse)
def api_sample():
    """Return sampled document IDs grouped by collection (from SAMPLE_DOCS env var)."""
    return SampleResponse(samples=config.sample_docs_by_collection())


@app.get("/api/collections")
def list_collections():
    """Return sorted list of collection names (top-level dirs in input)."""
    input_dir = Path(config.DATA_INPUT_DIR)
    if not input_dir.is_dir():
        return {"collections": []}
    return {"collections": sorted(p.name for p in input_dir.iterdir() if p.is_dir())}


@app.get("/api/collections/{collection}/documents")
def list_documents(collection: str):
    """Return sorted list of document names within a collection."""
    _safe_id(collection)
    col_dir = Path(config.DATA_INPUT_DIR) / collection
    if not col_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
    return {"documents": sorted(p.name for p in col_dir.iterdir() if p.is_dir())}


@app.get("/api/sources", response_model=SourcesResponse)
def list_sources():
    """Return sorted list of indexed source IDs as 'collection/document' composites."""
    transcribed_dir = Path(config.DATA_OUTPUT_DIR) / "transcribed"
    if not transcribed_dir.is_dir():
        return SourcesResponse(sources=[])
    sources: set[str] = set()
    for col_dir in transcribed_dir.iterdir():
        if not col_dir.is_dir():
            continue
        for txt in col_dir.glob("*.txt"):
            sources.add(f"{col_dir.name}/{txt.stem}")
        for sub in col_dir.iterdir():
            if sub.is_dir():
                sources.add(f"{col_dir.name}/{sub.name}")
    return SourcesResponse(sources=sorted(sources))


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

@app.get("/api/review/{collection}/{document}/pages")
def review_pages(collection: str, document: str):
    """List pages for a document with per-page availability flags."""
    _safe_id(collection)
    _safe_id(document)
    input_folder = Path(config.DATA_INPUT_DIR) / collection / document
    if not input_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Document '{collection}/{document}' not found in input.")
    out = Path(config.DATA_OUTPUT_DIR)
    stems = sorted(
        p.stem for p in input_folder.iterdir()
        if p.suffix.lower() in _REVIEW_EXTENSIONS
    )
    return [
        {
            "stem": stem,
            "has_imported": (
                (out / "imported" / collection / document / f"{stem}.txt").exists()
                or (out / "imported" / collection / f"{document}.txt").exists()
                or (out / "imported" / collection / f"{document}.xml").exists()
            ),
            "has_transcribed": (out / "transcribed" / collection / document / f"{stem}.txt").exists(),
            "has_finalized": (out / "finalized" / collection / document / f"{stem}.txt").exists(),
        }
        for stem in stems
    ]


@app.get("/api/review/{collection}/{document}/{page}/image")
def review_image(collection: str, document: str, page: str):
    """Serve the original scan image for a page."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    img = _find_image(collection, document, page)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Image not found for {collection}/{document}/{page}")
    return FileResponse(img)


@app.get("/api/review/{collection}/{document}/{page}/imported", response_class=PlainTextResponse)
def review_imported(collection: str, document: str, page: str):
    """Return the imported (pre-existing) transcript for a page or its parent document."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    out = Path(config.DATA_OUTPUT_DIR) / "imported"
    # Per-page imported file takes priority
    per_page = out / collection / document / f"{page}.txt"
    if per_page.exists():
        return PlainTextResponse(per_page.read_text(encoding="utf-8"))
    # Fall back to combined document-level transcript
    txt = out / collection / f"{document}.txt"
    if txt.exists():
        return PlainTextResponse(txt.read_text(encoding="utf-8"))
    xml = out / collection / f"{document}.xml"
    if xml.exists():
        from archival_htr.ingest import extract_text_from_page_xml
        return PlainTextResponse(extract_text_from_page_xml(xml))
    raise HTTPException(status_code=404, detail=f"No imported transcript for '{collection}/{document}'")


@app.get("/api/review/{collection}/{document}/{page}/transcribed", response_class=PlainTextResponse)
def review_transcribed(collection: str, document: str, page: str):
    """Return the LLM-refined transcript for a specific page."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "transcribed" / collection / document / f"{page}.txt"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No LLM transcript for '{collection}/{document}/{page}'")
    return PlainTextResponse(p.read_text(encoding="utf-8"))


@app.get("/api/review/{collection}/{document}/{page}/finalized", response_class=PlainTextResponse)
def review_get_finalized(collection: str, document: str, page: str):
    """Return the saved finalized transcript for a page."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "finalized" / collection / document / f"{page}.txt"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No finalized transcript for '{collection}/{document}/{page}'")
    return PlainTextResponse(p.read_text(encoding="utf-8"))


@app.post("/api/review/{collection}/{document}/{page}/finalized")
def review_save_finalized(collection: str, document: str, page: str, req: FinalizedRequest):
    """Save the finalized transcript for a page."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    p = Path(config.DATA_OUTPUT_DIR) / "finalized" / collection / document / f"{page}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.text, encoding="utf-8")
    return {"saved": True}


@app.get("/api/review/{collection}/{document}/{page}/metadata", response_model=PageMetadataResponse)
def review_metadata(collection: str, document: str, page: str):
    """Return taxonomy metadata for a specific page from its per-page CSV."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    csv_path = Path(config.DATA_OUTPUT_DIR) / "metadata" / collection / f"{document}_{page}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail=f"No metadata for '{collection}/{document}/{page}'")

    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Empty metadata for '{collection}/{document}/{page}'")

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
        job_application_type=_clean(row.get("job_application_type")),
        military_service_argument=_parse_bool(row.get("military_service_argument")),
        construction_works=_parse_bool(row.get("construction_works")),
        belgian_revolution_1830=_parse_bool(row.get("belgian_revolution_1830")),
        petitioner_name=_clean(row.get("petitioner_name")),
        petitioner_gender=_clean(row.get("petitioner_gender")),
        petitioner_occupation=_clean(row.get("petitioner_occupation")),
        petitioner_residence=_clean(row.get("petitioner_residence")),
        petitioner_birthplace=_clean(row.get("petitioner_birthplace")),
        petitioner_age=_clean(row.get("petitioner_age")),
        petitioner_writing_for=_clean(row.get("petitioner_writing_for")),
        petition_type=_clean(row.get("petition_type")),
    )


def _write_page_metadata_csv(collection: str, document: str, page: str, req: PageMetadataResponse):
    """Write a single-row per-page metadata CSV. Returns the normalized metadata object."""
    from archival_htr.ingest import METADATA_CSV_COLUMNS
    from archival_htr.llm_client import DocumentMetadata, normalize_metadata

    meta = normalize_metadata(DocumentMetadata(
        language=req.language or "unknown",
        single_page_or_part=req.single_page_or_part or "unknown",
        related_to_others=req.related_to_others or "unknown",
        date_submission_writing=req.date_submission_writing or "unknown",
        category=req.category or "Other",
        is_job_application=False,
        job_application_type=req.job_application_type or "unknown",
        military_service_argument=False,
        construction_works=False,
        belgian_revolution_1830=False,
        petitioner_name=req.petitioner_name or "unknown",
        petitioner_gender=req.petitioner_gender or "unknown",
        petitioner_occupation=req.petitioner_occupation or "unknown",
        petitioner_residence=req.petitioner_residence or "unknown",
        petitioner_birthplace=req.petitioner_birthplace or "unknown",
        petitioner_age=req.petitioner_age or "unknown",
        petitioner_writing_for=req.petitioner_writing_for or "unknown",
        petition_type=req.petition_type or "other",
    ))

    csv_path = Path(config.DATA_OUTPUT_DIR) / "metadata" / collection / f"{document}_{page}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    def _bool_str(v: Optional[bool]) -> str:
        if v is None:
            return ""
        return str(v).lower()

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(METADATA_CSV_COLUMNS)
        writer.writerow([
            document,
            page,
            meta.language,
            meta.single_page_or_part,
            meta.related_to_others,
            meta.date_submission_writing,
            meta.category,
            _bool_str(req.is_job_application),
            meta.job_application_type,
            _bool_str(req.military_service_argument),
            _bool_str(req.construction_works),
            _bool_str(req.belgian_revolution_1830),
            meta.petitioner_name,
            meta.petitioner_gender,
            meta.petitioner_occupation,
            meta.petitioner_residence,
            meta.petitioner_birthplace,
            meta.petitioner_age,
            meta.petitioner_writing_for,
            meta.petition_type,
        ])

    return meta


@app.put("/api/review/{collection}/{document}/{page}/metadata", response_model=PageMetadataResponse)
def review_update_metadata(collection: str, document: str, page: str, req: PageMetadataResponse):
    """Overwrite per-page metadata CSV and rebuild combined.csv."""
    _safe_id(collection)
    _safe_id(document)
    _safe_id(page)
    from archival_htr.ingest import combine_metadata_csvs

    meta = _write_page_metadata_csv(collection, document, page, req)

    try:
        combine_metadata_csvs(Path(config.DATA_OUTPUT_DIR))
    except Exception:
        pass

    # Best-effort ChromaDB update
    try:
        from archival_htr import rag, config as cfg
        source_id = f"{collection}/{document}"
        _chroma_client = rag._get_client()
        chroma_col = rag._get_collection(_chroma_client)
        result = chroma_col.get(where={"source": source_id})
        if result["ids"]:
            chroma_col.update(
                ids=result["ids"],
                metadatas=[{
                    **m,
                    "is_job_application": req.is_job_application,
                    "job_application_type": meta.job_application_type,
                    "military_service_argument": req.military_service_argument,
                    "construction_works": req.construction_works,
                    "belgian_revolution_1830": req.belgian_revolution_1830,
                    "petitioner_name": meta.petitioner_name,
                    "petitioner_gender": meta.petitioner_gender,
                    "petitioner_occupation": meta.petitioner_occupation,
                    "petitioner_residence": meta.petitioner_residence,
                    "petitioner_birthplace": meta.petitioner_birthplace,
                    "petitioner_age": meta.petitioner_age,
                    "petitioner_writing_for": meta.petitioner_writing_for,
                    "petition_type": meta.petition_type,
                } for m in result["metadatas"]],
            )
    except Exception:
        pass

    def _none_if_unknown(v: str) -> Optional[str]:
        return None if v in ("unknown", "") else v

    return PageMetadataResponse(
        language=_none_if_unknown(meta.language),
        category=meta.category,
        date_submission_writing=_none_if_unknown(meta.date_submission_writing),
        single_page_or_part=meta.single_page_or_part,
        related_to_others=meta.related_to_others,
        is_job_application=req.is_job_application,
        job_application_type=_none_if_unknown(meta.job_application_type),
        military_service_argument=req.military_service_argument,
        construction_works=req.construction_works,
        belgian_revolution_1830=req.belgian_revolution_1830,
        petitioner_name=_none_if_unknown(meta.petitioner_name),
        petitioner_gender=_none_if_unknown(meta.petitioner_gender),
        petitioner_occupation=_none_if_unknown(meta.petitioner_occupation),
        petitioner_residence=_none_if_unknown(meta.petitioner_residence),
        petitioner_birthplace=_none_if_unknown(meta.petitioner_birthplace),
        petitioner_age=_none_if_unknown(meta.petitioner_age),
        petitioner_writing_for=_none_if_unknown(meta.petitioner_writing_for),
        petition_type=meta.petition_type,
    )


@app.post("/api/review/{collection}/{document}/apply-metadata", response_model=ApplyMetadataResponse)
def review_apply_metadata(collection: str, document: str, req: PageMetadataResponse):
    """Apply one page's metadata to every page in the document, then rebuild combined.csv."""
    _safe_id(collection)
    _safe_id(document)
    from archival_htr.ingest import combine_metadata_csvs

    # Derive page list from existing per-page metadata CSVs (more robust than requiring input folder).
    metadata_dir = Path(config.DATA_OUTPUT_DIR) / "metadata" / collection
    prefix = document + "_"
    stems = sorted(
        f.stem[len(prefix):]
        for f in metadata_dir.glob(f"{prefix}*.csv")
        if f.stem != "combined"
    )
    if not stems:
        raise HTTPException(status_code=404, detail=f"No metadata found for '{collection}/{document}'")

    input_folder = Path(config.DATA_INPUT_DIR) / collection / document
    if not input_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Document '{collection}/{document}' not found in input.")

    stems = sorted(
        p.stem for p in input_folder.iterdir()
        if p.suffix.lower() in _REVIEW_EXTENSIONS
    )
    for stem in stems:
        _write_page_metadata_csv(collection, document, stem, req)

    try:
        combine_metadata_csvs(Path(config.DATA_OUTPUT_DIR))
    except Exception:
        pass

    return ApplyMetadataResponse(updated=len(stems))


@app.get("/api/overview", response_model=OverviewResponse)
def api_overview(
    language: Optional[str] = None,
    category: Optional[str] = None,
    single_page_or_part: Optional[str] = None,
    is_job_application: Optional[bool] = None,
    job_application_type: Optional[str] = None,
    military_service_argument: Optional[bool] = None,
    construction_works: Optional[bool] = None,
    belgian_revolution_1830: Optional[bool] = None,
    petitioner_name: Optional[str] = None,
    petitioner_gender: Optional[str] = None,
    petitioner_occupation: Optional[str] = None,
    petitioner_residence: Optional[str] = None,
    petitioner_birthplace: Optional[str] = None,
    petitioner_age: Optional[str] = None,
    petitioner_writing_for: Optional[str] = None,
    petition_type: Optional[str] = None,
):
    """Return all page metadata for the overview heatmap, with optional filtering."""
    metadata_root = Path(config.DATA_OUTPUT_DIR) / "metadata"
    pages: list[OverviewPage] = []

    def _contains(field: str | None, q: str) -> bool:
        return q.lower() in (field or "").lower()

    if metadata_root.is_dir():
        for col_dir in sorted(metadata_root.iterdir()):
            if not col_dir.is_dir():
                continue
            collection_name = col_dir.name
            combined_csv = col_dir / "combined.csv"
            csv_files = [combined_csv] if combined_csv.exists() else sorted(col_dir.glob("*.csv"))

            for csv_path in csv_files:
                with csv_path.open(encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        page = _parse_overview_row(row, collection_name)
                        if page is None:
                            continue
                        if language and (page.language or "").lower() != language.lower():
                            continue
                        if category and (page.category or "").lower() != category.lower():
                            continue
                        if single_page_or_part and (page.single_page_or_part or "").lower() != single_page_or_part.lower():
                            continue
                        if is_job_application is not None and page.is_job_application != is_job_application:
                            continue
                        if job_application_type and not _contains(page.job_application_type, job_application_type):
                            continue
                        if military_service_argument is not None and page.military_service_argument != military_service_argument:
                            continue
                        if construction_works is not None and page.construction_works != construction_works:
                            continue
                        if belgian_revolution_1830 is not None and page.belgian_revolution_1830 != belgian_revolution_1830:
                            continue
                        if petitioner_name and not _contains(page.petitioner_name, petitioner_name):
                            continue
                        if petitioner_gender and (page.petitioner_gender or "").lower() != petitioner_gender.lower():
                            continue
                        if petitioner_occupation and not _contains(page.petitioner_occupation, petitioner_occupation):
                            continue
                        if petitioner_residence and not _contains(page.petitioner_residence, petitioner_residence):
                            continue
                        if petitioner_birthplace and not _contains(page.petitioner_birthplace, petitioner_birthplace):
                            continue
                        if petitioner_age and not _contains(page.petitioner_age, petitioner_age):
                            continue
                        if petitioner_writing_for and not _contains(page.petitioner_writing_for, petitioner_writing_for):
                            continue
                        if petition_type and (page.petition_type or "").lower() != petition_type.lower():
                            continue
                        pages.append(page)

    return OverviewResponse(pages=pages, stats=_compute_overview_stats(pages))


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=(_STATIC_DIR / "index.html").read_text(encoding="utf-8"))
