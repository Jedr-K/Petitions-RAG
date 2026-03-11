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
    no_metadata: bool = typer.Option(False, "--no-metadata", help="Skip metadata annotation and combine step"),
):
    """Transcribe manuscript images to .txt. Use --doc to limit to specific documents."""
    from archival_htr.ingest import ingest_all
    ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
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
    no_metadata: bool = typer.Option(False, "--no-metadata", help="Skip metadata annotation and combine step"),
):
    """Run transcribe + metadata + index in one step. Use --doc to limit to specific documents."""
    from archival_htr.ingest import ingest_all
    from archival_htr.rag import index_all
    paths = ingest_all(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite=overwrite,
        doc_names=doc or None,
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
):
    """Run metadata annotation for all transcribed docs, then combine into one CSV."""
    from archival_htr import config
    from archival_htr.ingest import get_document_folders, run_metadata_for_document, combine_metadata_csvs

    input_dir = input_dir or Path(config.DATA_INPUT_DIR)
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)
    doc_folders = get_document_folders(input_dir)
    if doc:
        doc_folders = [f for f in doc_folders if f.name in doc]
    for folder in doc_folders:
        run_metadata_for_document(folder, output_dir, overwrite=overwrite)
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
):
    """Search the indexed manuscript corpus."""
    from archival_htr.rag import search as rag_search

    console.print(f"\n[bold]Searching for:[/bold] {query}\n")
    results = rag_search(query, n_results=n)

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


if __name__ == "__main__":
    app()
