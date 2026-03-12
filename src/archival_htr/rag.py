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


def _read_classification(output_dir: Path, doc_id: str) -> tuple[bool, bool]:
    """Look up is_job_application and military_service_argument from classification.csv.

    Returns (is_job_application, military_service_argument), defaulting to (False, False)
    if the file doesn't exist or the document has no entry.
    """
    csv_path = output_dir / "metadata" / "classification.csv"
    if not csv_path.exists():
        return False, False
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("source_doc") == doc_id:
                return (
                    row.get("is_job_application", "false").lower() == "true",
                    row.get("military_service_argument", "false").lower() == "true",
                )
    return False, False


def index_document(txt_path: Path, overwrite: bool = False):
    """Chunk and embed a transcribed .txt file into ChromaDB."""
    client = _get_client()
    collection = _get_collection(client)
    doc_id = txt_path.stem

    # Check if already indexed
    existing = collection.get(where={"source": doc_id}, limit=1)
    if existing["ids"] and not overwrite:
        console.print(f"[yellow]Already indexed[/yellow] {doc_id}, skipping.")
        return

    # Delete old chunks if overwriting
    if overwrite:
        collection.delete(where={"source": doc_id})

    text = txt_path.read_text(encoding="utf-8")
    chunks = _chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)

    # Read classification flags (written by `reclassify` or available from metadata CSVs)
    output_dir = txt_path.parent.parent
    is_job_app, military_svc = _read_classification(output_dir, doc_id)

    console.print(f"[cyan]Indexing[/cyan] {doc_id} ({len(chunks)} chunks)...")

    for i, chunk in enumerate(chunks):
        embedding = embed_text(chunk)
        collection.add(
            ids=[f"{doc_id}::chunk_{i}"],
            embeddings=[embedding],
            documents=[chunk],
            metadatas=[{
                "source": doc_id,
                "chunk": i,
                "is_job_application": is_job_app,
                "military_service_argument": military_svc,
            }],
        )

    console.print(f"[green]Indexed[/green] {doc_id}")


def index_all(output_dir: Path = None, overwrite: bool = False):
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    gemini_dir = output_dir / "transcribed"
    if gemini_dir.is_dir():
        txt_files = sorted(gemini_dir.glob("*.txt"))
        console.print(f"Searching in {gemini_dir} for .txt files...")
    else:
        txt_files = sorted(output_dir.glob("*.txt"))
        console.print(f"Searching in {output_dir} for .txt files...")
    console.print(f"Found [bold]{len(txt_files)}[/bold] transcription(s) to index\n")
    for f in txt_files:
        index_document(f, overwrite=overwrite)


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
        clauses.append({"source": source})
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
