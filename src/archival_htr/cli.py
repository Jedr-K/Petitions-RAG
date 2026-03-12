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
):
    """Transcribe manuscript images to .txt. Use --doc to limit to specific documents, --page for specific pages."""
    from archival_htr.ingest import ingest_all
    ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
        page_stems=page or None,
        run_metadata=not no_metadata,
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
):
    """Run transcribe + metadata + index in one step. Use --doc/--page to limit scope."""
    from archival_htr.ingest import ingest_all
    from archival_htr.rag import index_all
    paths = ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
        page_stems=page or None,
        run_metadata=not no_metadata,
    )
    if paths:
        index_all(output_dir=output_dir or paths[0].parent.parent, overwrite=overwrite)


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
        doc_folders = [f for f in doc_folders if f.name in doc]
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
    metadata_dir.mkdir(parents=True, exist_ok=True)
    classification_csv = metadata_dir / "classification.csv"

    # Load existing classifications to support --overwrite logic
    existing: dict[str, dict] = {}
    if classification_csv.exists():
        with open(classification_csv, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                existing[row["source_doc"]] = row

    txt_files = sorted(transcribed_dir.glob("*.txt"))
    if doc:
        txt_files = [f for f in txt_files if f.stem in doc]
    if not txt_files:
        console.print("[yellow]No transcripts found to classify.[/yellow]")
        raise typer.Exit()

    console.print(f"Classifying [bold]{len(txt_files)}[/bold] document(s)...\n")

    updated = dict(existing)
    for txt_path in txt_files:
        doc_id = txt_path.stem
        if doc_id in existing and not overwrite:
            console.print(f"[dim]Already classified[/dim] {doc_id}, skipping.")
            continue
        transcript = txt_path.read_text(encoding="utf-8")
        console.print(f"[cyan]Classifying[/cyan] {doc_id}...")
        try:
            result = classify_document(transcript)
        except Exception as e:
            console.print(f"[red]Error classifying {doc_id}:[/red] {e}")
            continue
        updated[doc_id] = {
            "source_doc": doc_id,
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

    # Write classification.csv
    columns = ["source_doc", "is_job_application", "military_service_argument", "reasoning"]
    with open(classification_csv, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in sorted(updated.values(), key=lambda r: r["source_doc"]):
            w.writerow(row)
    console.print(f"\n[green]Written[/green] → {classification_csv} ({len(updated)} entries)\n")

    # Re-index affected documents so ChromaDB flags are up to date
    to_reindex = [f for f in txt_files if f.stem in updated]
    if to_reindex:
        console.print(f"Re-indexing [bold]{len(to_reindex)}[/bold] document(s) with updated flags...")
        for txt_path in to_reindex:
            index_document(txt_path, overwrite=True)


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
    combined = transcribed_dir / f"{source}.txt"
    if combined.exists():
        context = combined.read_text(encoding="utf-8")
        console.print(f"[dim]Loaded[/dim] {combined} ({len(context):,} chars)\n")
    else:
        subdir = transcribed_dir / source
        if not subdir.is_dir():
            console.print(f"[red]No transcript found for source '{source}'[/red] (looked for {combined} or {subdir}/)")
            raise typer.Exit(1)
        parts = sorted(subdir.glob("*.txt"))
        if not parts:
            console.print(f"[red]No .txt files in[/red] {subdir}")
            raise typer.Exit(1)
        context = "\n\n".join(f.read_text(encoding="utf-8") for f in parts)
        console.print(f"[dim]Loaded[/dim] {len(parts)} files from {subdir} ({len(context):,} chars)\n")

    console.print("[bold]Question:[/bold]", question, "\n")
    answer = query_with_context(context, question)
    console.print("[bold]Answer:[/bold]\n", answer)


if __name__ == "__main__":
    app()
