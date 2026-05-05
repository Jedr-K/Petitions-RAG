import csv
from pathlib import Path
from rich.console import Console
import chromadb
from chromadb.config import Settings
from archival_htr import config  # In Python, when you import a module (or package), all of its public (non-underscore-prefixed) attributes, functions, and classes become accessible as attributes of that module object. There's no need for explicit exports like in JavaScript; everything defined at the top level of src/archival_htr/config.py (unless its name starts with _) is exposed as config.* here.
from archival_htr.llm_client import embed_text, embed_query

console = Console()


def _get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=config.CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False),
    )


def _get_collection(client: chromadb.ClientAPI):
    return client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, chunk_size - overlap)  # guard against zero/negative step
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _read_classification(output_dir: Path, collection: str, document: str) -> tuple[bool, bool]:
    """Look up is_job_application and military_service_argument for a document.

    Checks metadata/{collection}/classification.csv first, then falls back to
    metadata/{collection}/combined.csv. Returns (False, False) if not found.
    """
    col_meta = output_dir / "metadata" / collection
    for filename in ("classification.csv", "combined.csv"):
        csv_path = col_meta / filename
        if not csv_path.exists():
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("source_doc") == document:
                    return (
                        row.get("is_job_application", "false").lower() == "true",
                        row.get("military_service_argument", "false").lower() == "true",
                    )
    return False, False


def _build_index_text(output_dir: Path, collection: str, document: str) -> str:
    """Build indexable text for a document, preferring finalized pages over transcribed."""
    from archival_htr.ingest import parse_transcribed_pages
    from pathlib import Path as _Path
    transcribed_combined = output_dir / "transcribed" / collection / f"{document}.txt"
    if not transcribed_combined.exists():
        return ""
    pages = parse_transcribed_pages(transcribed_combined)
    finalized_dir = output_dir / "finalized" / collection / document
    parts = []
    for page_name, transcribed_text in pages:
        page_stem = _Path(page_name).stem
        finalized_path = finalized_dir / f"{page_stem}.txt"
        text = finalized_path.read_text(encoding="utf-8", errors="replace") if finalized_path.exists() else transcribed_text
        parts.append(f"--- {page_name} ---\n{text}")
    return "\n\n".join(parts)


def index_document(collection: str, document: str, output_dir: Path, overwrite: bool = False):
    """Chunk and embed a document into ChromaDB using finalized text where available."""
    client = _get_client()
    col = _get_collection(client)
    source_id = f"{collection}/{document}"

    existing = col.get(where={"source": source_id}, limit=1)
    if existing["ids"] and not overwrite:
        console.print(f"[yellow]Already indexed[/yellow] {source_id}, skipping.")
        return

    if overwrite:
        col.delete(where={"source": source_id})

    text = _build_index_text(output_dir, collection, document)
    if not text:
        console.print(f"[yellow]No text to index[/yellow] for {source_id}")
        return

    chunks = _chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    is_job_app, military_svc = _read_classification(output_dir, collection, document)

    console.print(f"[cyan]Indexing[/cyan] {source_id} ({len(chunks)} chunks)...")

    for i, chunk in enumerate(chunks):
        embedding = embed_text(chunk)
        col.add(
            ids=[f"{collection}::{document}::chunk_{i}"],
            embeddings=[embedding],
            documents=[chunk],
            metadatas=[{
                "collection": collection,
                "document": document,
                "source": source_id,
                "chunk": i,
                "is_job_application": is_job_app,
                "military_service_argument": military_svc,
            }],
        )

    console.print(f"[green]Indexed[/green] {source_id}")


def index_all(output_dir: Path = None, overwrite: bool = False):
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    transcribed_dir = output_dir / "transcribed"
    pairs: list[tuple[str, str]] = []
    if transcribed_dir.is_dir():
        for col_dir in sorted(transcribed_dir.iterdir()):
            if col_dir.is_dir():
                for txt in sorted(col_dir.glob("*.txt")):
                    if not txt.name.startswith("."):
                        pairs.append((col_dir.name, txt.stem))
    console.print(f"Found [bold]{len(pairs)}[/bold] transcription(s) to index\n")
    for col, doc in pairs:
        index_document(col, doc, output_dir, overwrite=overwrite)


def search(
    query: str,
    n_results: int = 5,
    source: str | None = None,
    job_application: bool | None = None,
    military_service: bool | None = None,
) -> list[dict]:
    """Search the corpus. Returns ranked list of result dicts.

    Args:
        query: Natural language search query.
        n_results: Maximum number of results to return.
        source: If set, restrict results to chunks from this document (stem name).
        job_application: If True/False, filter by is_job_application flag.
        military_service: If True/False, filter by military_service_argument flag.
    """
    client = _get_client()
    collection = _get_collection(client)

    clauses = []
    if source is not None:
        if "/" in source:
            clauses.append({"source": source})
        else:
            clauses.append({"collection": source})
    if job_application is not None:
        clauses.append({"is_job_application": job_application})
    if military_service is not None:
        clauses.append({"military_service_argument": military_service})
    where = {"$and": clauses} if len(clauses) > 1 else clauses[0] if clauses else None

    query_embedding = embed_query(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "source": meta["source"],
            "chunk": meta["chunk"],
            "score": round(1 - dist, 4),  # cosine similarity
            "text": doc,
        })

    return output
