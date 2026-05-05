import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import box

app = typer.Typer(
    name="archival-htr",
    help="HTR pipeline for historical manuscripts. Transcribe → Index → Search.",
    add_completion=False,
)
console = Console()


@app.command()
def transcribe(
    input_dir: Path = typer.Option(None, "--input", "-i", help="Folder of document subfolders"),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Where to write .txt files"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-transcribe already-done docs"),
    doc: list[str] = typer.Option([], "--doc", "-d", help="Process only these subfolder names; omit for all"),
    page: list[str] = typer.Option([], "--page", "-p", help="Process only these page stems (e.g. 14245707_0051_113628229); omit for all"),
    no_metadata: bool = typer.Option(False, "--no-metadata", help="Skip metadata annotation and combine step"),
    imported_only: bool = typer.Option(False, "--imported-only", help="Skip LLM transcription; use existing imported transcripts only"),
    backend: str = typer.Option(None, "--backend", help="Override backend: 'ollama' or 'gemini' (default: from env BACKEND)"),
):
    """Transcribe manuscript images to .txt. Use --doc to limit to specific documents, --page for specific pages."""
    import os
    from archival_htr.ingest import ingest_all
    if backend:
        os.environ["BACKEND"] = backend
    ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
        page_stems=page or None,
        run_metadata=not no_metadata,
        skip_transcription=imported_only,
    )


@app.command()
def index(
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing .txt files"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-index already-indexed docs"),
):
    """Chunk and embed transcribed .txt files into the vector store."""
    from archival_htr.rag import index_all
    index_all(output_dir=output_dir, overwrite=overwrite)


@app.command()
def ingest(
    input_dir: Path = typer.Option(None, "--input", "-i"),
    output_dir: Path = typer.Option(None, "--output", "-o"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    doc: list[str] = typer.Option([], "--doc", "-d", help="Process only these subfolder names; omit for all"),
    page: list[str] = typer.Option([], "--page", "-p", help="Process only these page stems; omit for all"),
    no_metadata: bool = typer.Option(False, "--no-metadata", help="Skip metadata annotation and combine step"),
    imported_only: bool = typer.Option(False, "--imported-only", help="Skip LLM transcription; use existing imported transcripts only"),
    backend: str = typer.Option(None, "--backend", help="Override backend: 'ollama' or 'gemini' (default: from env BACKEND)"),
):
    """Run transcribe + metadata + index in one step. Use --doc/--page to limit scope."""
    import os
    from archival_htr.ingest import ingest_all
    from archival_htr.rag import index_all
    if backend:
        os.environ["BACKEND"] = backend
    paths = ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
        page_stems=page or None,
        run_metadata=not no_metadata,
        skip_transcription=imported_only,
    )
    if paths:
        # paths[0] = output/transcribed/{collection}/{document}.txt → .parent×3 = output/
        index_all(output_dir=output_dir or paths[0].parent.parent.parent, overwrite=overwrite)


@app.command()
def metadata(
    input_dir: Path = typer.Option(None, "--input", "-i", help="Folder of document subfolders (for page images)"),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing transcribed/ and metadata/"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-annotate and overwrite existing metadata CSVs"),
    doc: list[str] = typer.Option([], "--doc", "-d", help="Process only these document names; omit for all"),
    page: list[str] = typer.Option([], "--page", "-p", help="Process only these page stems; omit for all"),
):
    """Run metadata annotation for all transcribed docs, then combine into one CSV."""
    from archival_htr import config
    from archival_htr.ingest import get_document_folders, run_metadata_for_document, combine_metadata_csvs

    input_dir = input_dir or Path(config.DATA_INPUT_DIR)
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    doc_folders = get_document_folders(input_dir)
    if doc:
        doc_set = set(doc)
        doc_folders = [
            f for f in doc_folders
            if f.name in doc_set
            or f"{f.parent.name}/{f.name}" in doc_set
            or f.parent.name in doc_set
        ]
    page_set = set(page) if page else None
    for folder in doc_folders:
        run_metadata_for_document(folder, output_dir, overwrite=overwrite, page_stems=page_set)
    combine_metadata_csvs(output_dir)


@app.command()
def combine_metadata(
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing metadata/"),
):
    """Merge all single-line metadata CSVs in output/metadata/ into metadata/combined.csv."""
    from archival_htr import config
    from archival_htr.ingest import combine_metadata_csvs

    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    combine_metadata_csvs(output_dir)


@app.command()
def audit(
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing transcribed/"),
    collection: str | None = typer.Option(None, "--collection", "-c", help="Limit scan to one collection"),
):
    """List combined transcript files that contain pages with empty transcripts."""
    from archival_htr import config
    from archival_htr.ingest import parse_transcribed_pages

    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    transcribed_dir = output_dir / "transcribed"
    if not transcribed_dir.exists():
        typer.echo(f"No transcribed directory found at {transcribed_dir}")
        raise typer.Exit(1)

    found_any = False
    glob_root = transcribed_dir / collection if collection else transcribed_dir

    for txt_file in sorted(glob_root.rglob("*.txt")):
        # Combined files live exactly 2 levels below transcribed_dir.
        # Per-page files are 3 levels deep — skip them.
        if txt_file.parent.parent != transcribed_dir:
            continue

        pages = parse_transcribed_pages(txt_file)
        if not pages:
            continue

        empty = [name.split(" ---")[0].strip() for name, text in pages if not text.strip()]
        if empty:
            found_any = True
            rel = txt_file.relative_to(transcribed_dir)
            typer.echo(f"{rel}  ({len(empty)}/{len(pages)} empty pages)")
            for name in empty:
                typer.echo(f"  - {name}")

    if not found_any:
        typer.echo("No empty transcripts found.")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    n: int = typer.Option(5, "--results", "-n", help="Number of results to return"),
    source: str | None = typer.Option(None, "--source", "-s", help="Limit to one document/collection (e.g. 14)"),
    job_application: bool | None = typer.Option(None, "--job-application/--no-job-application", help="Filter to job-application petitions only (or exclude them)"),
    military_service: bool | None = typer.Option(None, "--military-service/--no-military-service", help="Filter to documents citing military service as argument (or exclude them)"),
):
    """Search the indexed manuscript corpus."""
    from archival_htr.rag import search as rag_search

    n_results = n if n > 0 else 100  # -1 or 0 → cap at 100 for 'all in collection'
    console.print(f"\n[bold]Searching for:[/bold] {query}\n")
    filters = []
    if source:
        filters.append(f"source={source}")
    if job_application is not None:
        filters.append("job-application" if job_application else "no-job-application")
    if military_service is not None:
        filters.append("military-service" if military_service else "no-military-service")
    if filters:
        console.print(f"[dim]Filters: {', '.join(filters)}[/dim]\n")
    results = rag_search(query, n_results=n_results, source=source, job_application=job_application, military_service=military_service)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        raise typer.Exit()

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("Rank", style="dim", width=5)
    table.add_column("Source", style="bold cyan")
    table.add_column("Score", style="green", width=7)
    table.add_column("Excerpt")

    for i, r in enumerate(results, 1):
        excerpt = r["text"][:300].replace("\n", " ") + ("..." if len(r["text"]) > 300 else "")
        table.add_row(str(i), r["source"], str(r["score"]), excerpt)

    console.print(table)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural-language question about the corpus"),
    n: int = typer.Option(8, "--chunks", "-n", help="Number of retrieved passages to use as context"),
    source: str | None = typer.Option(None, "--source", "-s", help="Limit retrieval to one document/collection"),
    job_application: bool | None = typer.Option(None, "--job-application/--no-job-application", help="Filter to job-application petitions only"),
    military_service: bool | None = typer.Option(None, "--military-service/--no-military-service", help="Filter to documents citing military service"),
):
    """Ask a natural-language question about the corpus.

    Retrieves the most relevant passages via semantic search, then passes them
    as context to the LLM which synthesises a sourced answer.
    """
    from archival_htr.rag import search as rag_search
    from archival_htr.llm_client import query_with_context

    # 1. Retrieve relevant chunks
    chunks = rag_search(
        question,
        n_results=n,
        source=source,
        job_application=job_application,
        military_service=military_service,
    )
    if not chunks:
        console.print("[yellow]No relevant passages found in the corpus.[/yellow]")
        raise typer.Exit()

    # 2. Build labelled context string
    context_parts = [f"[Source: {r['source']}]\n{r['text']}" for r in chunks]
    context = "\n\n---\n\n".join(context_parts)
    sources_used = sorted({r["source"] for r in chunks})

    console.print(f"\n[bold]Question:[/bold] {question}")
    console.print(f"[dim]Using {len(chunks)} passage(s) from: {', '.join(sources_used)}[/dim]\n")

    # 3. Synthesise answer
    answer = query_with_context(context, question)

    console.print("[bold]Answer:[/bold]\n")
    console.print(answer)
    console.print(f"\n[dim]Sources consulted: {', '.join(sources_used)}[/dim]")


@app.command()
def reclassify(
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing transcribed/ and metadata/"),
    doc: list[str] = typer.Option([], "--doc", "-d", help="Process only these document names; omit for all"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-classify already-classified documents"),
):
    """Classify transcribed documents for job-application type and military-service argument.

    Reads existing transcripts (no images needed), calls the LLM, and writes
    output/metadata/classification.csv. Then re-indexes each document so
    ChromaDB chunk metadata includes the new flags (filterable with --job-application /
    --military-service on the search command).
    """
    import csv as _csv
    from archival_htr import config as cfg
    from archival_htr.llm_client import classify_document
    from archival_htr.rag import index_document

    out = output_dir or Path(cfg.DATA_OUTPUT_DIR)
    transcribed_dir = out / "transcribed"
    metadata_dir = out / "metadata"

    # Discover all (collection, document) pairs from transcribed/{collection}/{document}.txt
    doc_set = set(doc) if doc else None
    pairs: list[tuple[str, str, Path]] = []  # (collection, document, txt_path)
    if transcribed_dir.is_dir():
        for col_dir in sorted(transcribed_dir.iterdir()):
            if not col_dir.is_dir():
                continue
            for txt_path in sorted(col_dir.glob("*.txt")):
                col_name, doc_name = col_dir.name, txt_path.stem
                if doc_set and not (
                    doc_name in doc_set
                    or f"{col_name}/{doc_name}" in doc_set
                    or col_name in doc_set
                ):
                    continue
                pairs.append((col_name, doc_name, txt_path))

    if not pairs:
        console.print("[yellow]No transcripts found to classify.[/yellow]")
        raise typer.Exit()

    console.print(f"Classifying [bold]{len(pairs)}[/bold] document(s)...\n")

    # Load existing per-collection classification CSVs
    existing: dict[str, dict[str, dict]] = {}  # col -> {doc -> row}
    for col_name, doc_name, _ in pairs:
        if col_name not in existing:
            csv_path = metadata_dir / col_name / "classification.csv"
            existing[col_name] = {}
            if csv_path.exists():
                with open(csv_path, newline="", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        existing[col_name][row["source_doc"]] = row

    updated: dict[str, dict[str, dict]] = {k: dict(v) for k, v in existing.items()}
    for col_name, doc_name, txt_path in pairs:
        col_existing = existing.get(col_name, {})
        if doc_name in col_existing and not overwrite:
            console.print(f"[dim]Already classified[/dim] {col_name}/{doc_name}, skipping.")
            continue
        transcript = txt_path.read_text(encoding="utf-8")
        console.print(f"[cyan]Classifying[/cyan] {col_name}/{doc_name}...")
        try:
            result = classify_document(transcript)
        except Exception as e:
            console.print(f"[red]Error classifying {col_name}/{doc_name}:[/red] {e}")
            continue
        updated.setdefault(col_name, {})[doc_name] = {
            "source_doc": doc_name,
            "is_job_application": str(result["is_job_application"]).lower(),
            "military_service_argument": str(result["military_service_argument"]).lower(),
            "reasoning": result["reasoning"],
        }
        label = []
        if result["is_job_application"]:
            label.append("[green]job application[/green]")
        if result["military_service_argument"]:
            label.append("[yellow]military service[/yellow]")
        tag = " + ".join(label) if label else "[dim]neither[/dim]"
        console.print(f"  → {tag}: {result['reasoning']}")

    # Write per-collection classification.csv files
    columns = ["source_doc", "is_job_application", "military_service_argument", "reasoning"]
    for col_name, col_rows in updated.items():
        col_meta_dir = metadata_dir / col_name
        col_meta_dir.mkdir(parents=True, exist_ok=True)
        classification_csv = col_meta_dir / "classification.csv"
        with open(classification_csv, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            for row in sorted(col_rows.values(), key=lambda r: r["source_doc"]):
                w.writerow(row)
        console.print(f"[green]Written[/green] → {classification_csv} ({len(col_rows)} entries)")

    console.print()

    # Re-index affected documents so ChromaDB flags are up to date
    to_reindex = [(c, d) for c, d, _ in pairs if d in updated.get(c, {})]
    if to_reindex:
        console.print(f"Re-indexing [bold]{len(to_reindex)}[/bold] document(s) with updated flags...")
        for col_name, doc_name in to_reindex:
            index_document(col_name, doc_name, out, overwrite=True)


@app.command()
def query(
    question: str = typer.Argument(..., help="Question to ask about the transcript"),
    source: str = typer.Option(..., "--source", "-s", help="Collection id (e.g. 14) whose transcript to use"),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder containing transcribed/"),
):
    """Put the full transcript for one collection in context and ask the model. No RAG."""
    from archival_htr import config
    from archival_htr.llm_client import query_with_context

    out = output_dir or Path(config.DATA_OUTPUT_DIR)
    transcribed_dir = out / "transcribed"
    # source is expected as "collection/document" (e.g. "14/1")
    collection_part, _, document_part = source.partition("/")
    if document_part:
        combined = transcribed_dir / collection_part / f"{document_part}.txt"
        subdir = transcribed_dir / collection_part / document_part
    else:
        # bare name: search across all collections
        combined = None
        subdir = None
        for col_dir in sorted(transcribed_dir.iterdir()):
            if col_dir.is_dir():
                candidate = col_dir / f"{source}.txt"
                if candidate.exists():
                    combined = candidate
                    break
                candidate_dir = col_dir / source
                if candidate_dir.is_dir():
                    subdir = candidate_dir
                    break
    if combined and combined.exists():
        context = combined.read_text(encoding="utf-8")
        console.print(f"[dim]Loaded[/dim] {combined} ({len(context):,} chars)\n")
    elif subdir and subdir.is_dir():
        parts = sorted(subdir.glob("*.txt"))
        if not parts:
            console.print(f"[red]No .txt files in[/red] {subdir}")
            raise typer.Exit(1)
        context = "\n\n".join(f.read_text(encoding="utf-8") for f in parts)
        console.print(f"[dim]Loaded[/dim] {len(parts)} files from {subdir} ({len(context):,} chars)\n")
    else:
        console.print(f"[red]No transcript found for source '{source}'[/red]")
        raise typer.Exit(1)

    console.print("[bold]Question:[/bold]", question, "\n")
    answer = query_with_context(context, question)
    console.print("[bold]Answer:[/bold]\n", answer)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn auto-reload (dev only)"),
):
    """Start the FastAPI web UI server on the given port."""
    import uvicorn
    uvicorn.run("archival_htr.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
