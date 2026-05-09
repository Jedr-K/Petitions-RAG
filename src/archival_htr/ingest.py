import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path
import shutil
from tqdm import tqdm
from rich.console import Console
from archival_htr import config
from archival_htr.llm_client import transcribe_image, annotate_metadata, DocumentMetadata

console = Console()

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

IMPORTED_SUBDIR = "imported"
TRANSCRIBED_SUBDIR = "transcribed"
METADATA_SUBDIR = "metadata"
METADATA_COMBINED_FILENAME = "combined.csv"

METADATA_CSV_COLUMNS = [
    "source_doc",
    "source_page",
    "language",
    "single_page_or_part",
    "related_to_others",
    "date_submission_writing",
    "category",
    "is_job_application",
    "job_application_type",
    "military_service_argument",
    "construction_works",
    "belgian_revolution_1830",
    "petitioner_name",
    "petitioner_gender",
    "petitioner_occupation",
    "petitioner_residence",
    "petitioner_birthplace",
    "petitioner_age",
    "petitioner_writing_for",
    "petition_type",
]

# Fields that are document-level (same for every page) and safe to propagate.
# source_doc, source_page, single_page_or_part, related_to_others are intentionally excluded.
PROPAGATABLE_FIELDS = [
    "language",
    "date_submission_writing",
    "category",
    "is_job_application",
    "job_application_type",
    "military_service_argument",
    "construction_works",
    "belgian_revolution_1830",
    "petitioner_name",
    "petitioner_gender",
    "petitioner_occupation",
    "petitioner_residence",
    "petitioner_birthplace",
    "petitioner_age",
    "petitioner_writing_for",
    "petition_type",
]


def get_txt_path(folder: Path) -> Path | None:
    """Return the .txt file in this document folder if present (same stem as folder name or single .txt)."""
    same_name = folder / f"{folder.name}.txt"
    if same_name.exists():
        return same_name
    txt_files = list(folder.glob("*.txt"))
    return txt_files[0] if len(txt_files) == 1 else None


def get_xml_path(folder: Path) -> Path | None:
    """Return the .xml file in this document folder if present (same stem as folder name or single .xml)."""
    same_name = folder / f"{folder.name}.xml"
    if same_name.exists():
        return same_name
    xml_files = list(folder.glob("*.xml"))
    return xml_files[0] if len(xml_files) == 1 else None


def extract_text_from_page_xml(xml_path: Path) -> str:
    """Extract line text from PAGE XML (all <TextEquiv><Unicode>...</Unicode></TextEquiv>)."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # PAGE XML declares a namespace like {http://schema.primaresearch.org/PAGE/...}
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        parts = [
            elem.text.strip()
            for elem in root.iter(f"{ns}Unicode")
            if elem.text and elem.text.strip()
        ]
        return "\n".join(parts)
    except ET.ParseError as e:
        console.print(f"[yellow]Warning: Could not parse XML {xml_path.name}: {e}. Falling back to regex.[/yellow]")
        text = xml_path.read_text(encoding="utf-8")
        parts = re.findall(r"<Unicode>(.*?)</Unicode>", text, re.DOTALL)
        return "\n".join(p.strip() for p in parts if p.strip())


def get_document_folders(input_dir: Path) -> list[Path]:
    """
    Returns document leaf folders from input_dir/{collection}/{document}/.
    Each returned path is the leaf document directory; caller derives collection
    via folder.parent.name and document via folder.name.
    """
    folders = []
    for col_dir in sorted(input_dir.iterdir()):
        if col_dir.is_dir():
            for doc_dir in sorted(col_dir.iterdir()):
                if doc_dir.is_dir():
                    folders.append(doc_dir)
    return folders


def get_pages(folder: Path) -> list[Path]:
    pages = sorted([
        f for f in folder.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])
    return pages


def _copy_imported_to(doc_folder: Path, doc_name: str, imported_dir: Path) -> None:
    """Copy imported .txt and .xml from doc_folder to imported_dir."""
    imported_dir.mkdir(parents=True, exist_ok=True)
    txt_src = get_txt_path(doc_folder)
    if txt_src is not None:
        shutil.copy2(txt_src, imported_dir / f"{doc_name}.txt")
        console.print(f"[dim]Copied imported[/dim] → {imported_dir / f'{doc_name}.txt'}")
    xml_src = get_xml_path(doc_folder)
    if xml_src is not None:
        shutil.copy2(xml_src, imported_dir / f"{doc_name}.xml")
        console.print(f"[dim]Copied imported[/dim] → {imported_dir / f'{doc_name}.xml'}")


def _build_existing_context(doc_folder: Path) -> str:
    """Build a single context string from .txt and .xml in the document folder for Gemini."""
    parts = []
    txt_path = get_txt_path(doc_folder)
    if txt_path is not None:
        parts.append(f"Existing .txt transcription:\n{txt_path.read_text(encoding='utf-8')}")
    xml_path = get_xml_path(doc_folder)
    if xml_path is not None:
        xml_text = extract_text_from_page_xml(xml_path)
        if xml_text:
            parts.append(f"Existing PAGE/XML line text:\n{xml_text}")
    return "\n\n".join(parts) if parts else ""


def _write_single_page_txt(text: str, page: Path, gemini_dir: Path, doc_name: str) -> None:
    """Write one .txt file per transcribed page under gemini_dir/{doc_name}/{page.stem}.txt."""
    console.print(f"[green]Writing[/green] → {gemini_dir / f'{doc_name}/{page.stem}.txt'}")
    out_dir = gemini_dir / doc_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{page.stem}.txt").write_text(text, encoding="utf-8")


def transcribe_document(
    doc_folder: Path,
    output_dir: Path,
    overwrite: bool = False,
    page_stems: set[str] | None = None,
    skip_transcription: bool = False,
) -> Path:
    """
    Copy imported .txt/.xml to output_dir/imported/{collection}/; optionally transcribe with
    Gemini and save to output_dir/transcribed/{collection}/{doc_name}.txt.
    If skip_transcription=True, copies imported transcripts as-is without LLM refinement.
    Returns path to the .txt file. If page_stems is set, only those pages are processed.
    """
    collection = doc_folder.parent.name
    doc_name = doc_folder.name
    imported_dir = output_dir / IMPORTED_SUBDIR / collection
    gemini_dir = output_dir / TRANSCRIBED_SUBDIR / collection
    gemini_txt_path = gemini_dir / f"{doc_name}.txt"

    _copy_imported_to(doc_folder, doc_name, imported_dir)

    # If skip_transcription is True, copy imported transcripts to transcribed output and return
    if skip_transcription:
        existing_context = _build_existing_context(doc_folder)
        if existing_context:
            all_pages = get_pages(doc_folder)
            page_to_text = dict(parse_transcribed_pages(imported_dir / f"{doc_name}.txt")) if (imported_dir / f"{doc_name}.txt").exists() else {}
            if not page_to_text:
                # Try XML
                xml_path = get_xml_path(doc_folder)
                if xml_path:
                    xml_text = extract_text_from_page_xml(xml_path)
                    for page in all_pages:
                        page_to_text[page.name] = xml_text
            gemini_dir.mkdir(parents=True, exist_ok=True)
            full_text_parts = [f"--- {p.name} ---\n{page_to_text.get(p.name, '')}" for p in all_pages]
            gemini_txt_path.write_text("\n\n".join(full_text_parts), encoding="utf-8")
            console.print(f"[green]Copied imported transcripts[/green] → {gemini_txt_path}")
            return gemini_txt_path
        else:
            console.print(f"[yellow]No imported transcripts found[/yellow] for {doc_name}, skipping.")
            return gemini_txt_path

    all_pages = get_pages(doc_folder)
    if not all_pages:
        console.print(f"[red]No images found[/red] in {doc_folder}")
        return gemini_txt_path

    if page_stems:
        pages = [p for p in all_pages if p.stem in page_stems]
        if not pages:
            console.print(f"[yellow]No matching pages for stems[/yellow] {page_stems} in {doc_name}")
            return gemini_txt_path
    else:
        pages = all_pages
        # Check if all current pages are already transcribed before skipping
        if gemini_txt_path.exists() and not overwrite:
            existing_pages = set(name.split(" ---")[0].strip() for name, _ in parse_transcribed_pages(gemini_txt_path))
            current_pages = set(p.name for p in all_pages)
            if existing_pages == current_pages:
                console.print(f"[yellow]Skipping[/yellow] {doc_name} (all pages already transcribed, use --overwrite to redo)")
                return gemini_txt_path
            # If there are new pages, keep only those for processing
            pages = [p for p in all_pages if p.name not in existing_pages]
            if pages:
                console.print(f"[cyan]Found[/cyan] {len(pages)} new page(s) in {doc_name}")
            else:
                return gemini_txt_path

    existing_context = _build_existing_context(doc_folder)
    if existing_context:
        console.print(f"[bold cyan]Transcribing[/bold cyan] {doc_name} ({len(pages)} page(s)) [with existing .txt/.xml context]")
    else:
        console.print(f"[bold cyan]Transcribing[/bold cyan] {doc_name} ({len(pages)} page(s))")

    # Load existing combined transcript when doing partial update
    page_to_text: dict[str, str] = {}
    if page_stems and gemini_txt_path.exists():
        page_to_text = dict(parse_transcribed_pages(gemini_txt_path))
    for page in tqdm(pages, desc=doc_name, unit="page"):
        try:
            text = transcribe_image(page, existing_context=existing_context)
            _write_single_page_txt(text, page, gemini_dir, doc_name)
            page_to_text[page.name] = text
        except KeyboardInterrupt:
            raise
        except Exception as e:
            console.print(f"[red]Error on {page.name}:[/red] {e}")
            page_to_text[page.name] = ""

    gemini_dir.mkdir(parents=True, exist_ok=True)
    # Write combined file in document page order
    full_text_parts = [f"--- {p.name} ---\n{page_to_text.get(p.name, '')}" for p in all_pages]
    gemini_txt_path.write_text("\n\n".join(full_text_parts), encoding="utf-8")
    console.print(f"[green]Saved[/green] → {gemini_txt_path}")

    xml_src = get_xml_path(doc_folder)
    if xml_src is not None:
        shutil.copy2(xml_src, gemini_dir / f"{doc_name}.xml")
        console.print(f"[green]Copied[/green] → {gemini_dir / f'{doc_name}.xml'}")

    return gemini_txt_path


# ---------------------------------------------------------------------------
# Metadata: one CSV per (image, transcript) pair, then combine
# ---------------------------------------------------------------------------

def _write_metadata_csv(
    meta: DocumentMetadata,
    output_path: Path,
    source_doc: str,
    source_page: str,
) -> None:
    """Write a single-line CSV (header + one row) for this metadata entry."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(METADATA_CSV_COLUMNS)
        w.writerow([
            source_doc,
            source_page,
            meta.language,
            meta.single_page_or_part,
            meta.related_to_others,
            meta.date_submission_writing,
            meta.category,
            str(meta.is_job_application).lower(),
            meta.job_application_type,
            str(meta.military_service_argument).lower(),
            str(meta.construction_works).lower(),
            str(meta.belgian_revolution_1830).lower(),
            meta.petitioner_name,
            meta.petitioner_gender,
            meta.petitioner_occupation,
            meta.petitioner_residence,
            meta.petitioner_birthplace,
            meta.petitioner_age,
            meta.petitioner_writing_for,
            meta.petition_type,
        ])


def parse_transcribed_pages(txt_path: Path) -> list[tuple[str, str]]:
    """
    Parse a transcribed .txt file (format "--- page.name ---\\ntext") into
    (page_name, transcript) pairs. Returns list of (page_name, text).
    """
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    # Split by page delimiter; each segment is "page.name ---\ntranscript"
    segments = re.split(r"\n---\s+", text)
    result = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith("--- "):
            seg = seg[4:]
        if " ---\n" in seg:
            page_name, _, transcript = seg.partition(" ---\n")
            result.append((page_name.strip(), transcript.strip()))
        else:
            lines = seg.split("\n", 1)
            result.append((lines[0].strip(), lines[1].strip() if len(lines) > 1 else ""))
    return result


def annotate_and_write_metadata(
    image_path: Path,
    transcript: str,
    output_dir: Path,
    collection: str,
    doc_name: str,
    page_id: str,
    overwrite: bool = False,
    hints: dict | None = None,
) -> Path:
    """
    Run LLM metadata annotation for one (image, transcript) pair and write
    a single-line CSV under output_dir/metadata/{collection}/{doc_name}_{page_id}.csv.
    Returns path to the written CSV.
    """
    metadata_dir = output_dir / METADATA_SUBDIR / collection
    safe_page_id = re.sub(r"[^\w\-.]", "_", page_id)
    csv_path = metadata_dir / f"{doc_name}_{safe_page_id}.csv"
    if csv_path.exists() and not overwrite:
        console.print(f"[dim]Metadata exists[/dim] {csv_path.name}, skipping.")
        return csv_path
    meta = annotate_metadata(image_path, transcript, hints=hints)
    _write_metadata_csv(meta, csv_path, source_doc=doc_name, source_page=page_id)
    console.print(f"[green]Metadata[/green] → {csv_path}")
    return csv_path


def run_metadata_for_document(
    doc_folder: Path,
    output_dir: Path,
    overwrite: bool = False,
    page_stems: set[str] | None = None,
    hints: dict | None = None,
) -> list[Path]:
    """
    For each page in the document: load transcript from output_dir/transcribed/{collection}/{doc_name}.txt,
    run annotate_metadata(image, transcript), write one CSV to output_dir/metadata/{collection}/.
    If page_stems is set, only those pages are annotated. Returns list of written CSV paths.
    """
    collection = doc_folder.parent.name
    doc_name = doc_folder.name
    transcribed_path = output_dir / TRANSCRIBED_SUBDIR / collection / f"{doc_name}.txt"
    if not transcribed_path.exists():
        console.print(f"[yellow]No transcript[/yellow] for {doc_name}, skip metadata.")
        return []
    pages = get_pages(doc_folder)
    if not pages:
        return []
    if page_stems:
        pages = [p for p in pages if p.stem in page_stems]
        if not pages:
            return []
    page_transcripts = {name: text for name, text in parse_transcribed_pages(transcribed_path)}
    written = []
    for page_path in tqdm(pages, desc=f"Metadata {doc_name}", unit="page"):
        page_name = page_path.name
        transcript = page_transcripts.get(page_name, "")
        if not transcript:
            console.print(f"[yellow]No transcript for page[/yellow] {page_name}, skipping.")
            continue
        try:
            path = annotate_and_write_metadata(
                page_path, transcript, output_dir, collection, doc_name, page_path.stem,
                overwrite=overwrite, hints=hints,
            )
            written.append(path)
        except Exception as e:
            console.print(f"[red]Error metadata {page_name}:[/red] {e}")
    return written


def combine_metadata_csvs(output_dir: Path) -> list[Path]:
    """
    For each collection dir in output_dir/metadata/, merge all single-line CSVs
    (excluding combined.csv) into that collection's combined.csv.
    Returns list of written combined paths.
    """
    metadata_dir = output_dir / METADATA_SUBDIR
    combined_paths = []
    if not metadata_dir.is_dir():
        console.print("[yellow]No metadata directory found.[/yellow]")
        return combined_paths
    col_dirs = sorted(p for p in metadata_dir.iterdir() if p.is_dir())
    if not col_dirs:
        console.print("[yellow]No metadata CSVs to combine.[/yellow]")
        return combined_paths
    for col_dir in col_dirs:
        combined_path = col_dir / METADATA_COMBINED_FILENAME
        csv_files = sorted(f for f in col_dir.glob("*.csv") if f.name != METADATA_COMBINED_FILENAME)
        if not csv_files:
            continue
        rows = []
        header = None
        for f in csv_files:
            with open(f, newline="", encoding="utf-8", errors="replace") as fp:
                r = csv.reader(fp)
                row_header = next(r, None)
                if not row_header:
                    continue
                if header is None:
                    header = row_header
                n_cols = len(row_header)
                for row in r:
                    if row and len(row) == n_cols:
                        rows.append(row)
        with open(combined_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header or METADATA_CSV_COLUMNS)
            w.writerows(rows)
        console.print(f"[green]Combined[/green] → {combined_path} ({len(rows)} rows)")
        combined_paths.append(combined_path)
    return combined_paths


def propagate_metadata_within_document(
    collection: str,
    doc_name: str,
    output_dir: Path,
    fields: list[str] | None = None,
    source_page: str | None = None,
    overwrite_existing: bool = False,
) -> int:
    """Copy metadata fields from one page to all other pages of the same document.

    Args:
        collection: Collection name (subdirectory under metadata/).
        doc_name: Document name (prefix of per-page CSV files).
        output_dir: Root output directory containing metadata/.
        fields: Which fields to propagate. Defaults to PROPAGATABLE_FIELDS.
        source_page: Page stem to copy from. Defaults to the first page (alphabetically).
        overwrite_existing: If False (default), only fill fields that are blank in target pages.
    Returns:
        Number of page CSVs updated.
    """
    fields = fields or PROPAGATABLE_FIELDS
    invalid = [f for f in fields if f not in PROPAGATABLE_FIELDS]
    if invalid:
        raise ValueError(f"Non-propagatable field(s): {invalid}. Allowed: {PROPAGATABLE_FIELDS}")

    metadata_dir = output_dir / METADATA_SUBDIR / collection
    safe_doc = re.sub(r"[^\w\-.]", "_", doc_name)

    # Collect all per-page CSVs for this document (exclude combined.csv)
    page_csvs = sorted(
        f for f in metadata_dir.glob(f"{safe_doc}_*.csv")
        if f.name != METADATA_COMBINED_FILENAME
    )
    if not page_csvs:
        console.print(f"[yellow]No per-page CSVs found[/yellow] for {collection}/{doc_name}")
        return 0

    # Determine source CSV
    if source_page:
        safe_page = re.sub(r"[^\w\-.]", "_", source_page)
        src_path = metadata_dir / f"{safe_doc}_{safe_page}.csv"
        if not src_path.exists():
            raise FileNotFoundError(f"Source page CSV not found: {src_path}")
    else:
        src_path = page_csvs[0]

    # Read source row
    with open(src_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        src_rows = list(reader)
    if not src_rows:
        console.print(f"[yellow]Source CSV is empty:[/yellow] {src_path.name}")
        return 0
    src_row = src_rows[0]
    values = {field: src_row.get(field, "") for field in fields}

    console.print(f"[cyan]Propagating from[/cyan] {src_path.name}:")
    for field, val in values.items():
        console.print(f"  [dim]{field}[/dim] = {val!r}")

    updated = 0
    for csv_path in page_csvs:
        if csv_path == src_path:
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            header = reader.fieldnames or METADATA_CSV_COLUMNS
        if not rows:
            continue
        row = rows[0]
        changed = False
        for field, val in values.items():
            if field not in row:
                continue
            if overwrite_existing or not row[field].strip():
                if row[field] != val:
                    row[field] = val
                    changed = True
        if not changed:
            console.print(f"  [dim]No changes needed[/dim] → {csv_path.name}")
            continue
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            w.writerow(row)
        console.print(f"  [green]Updated[/green] → {csv_path.name}")
        updated += 1

    return updated


def ingest_all(
    input_dir: Path = None,
    output_dir: Path = None,
    overwrite: bool = False,
    doc_names: list[str] | None = None,
    page_stems: list[str] | None = None,
    run_metadata: bool = True,
    skip_transcription: bool = False,
    hints: dict | None = None,
) -> list[Path]:
    input_dir = input_dir or Path(config.DATA_INPUT_DIR)
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)

    doc_folders = get_document_folders(input_dir)
    if doc_names:
        doc_names_set = set(doc_names)
        doc_folders = [
            f for f in doc_folders
            if f.name in doc_names_set                              # by document name alone
            or f"{f.parent.name}/{f.name}" in doc_names_set        # by collection/document
            or f.parent.name in doc_names_set                      # by collection (all docs)
        ]
        if not doc_folders:
            console.print(f"[red]No matching document(s) for --doc {doc_names}[/red]")
            return []
        console.print(f"Targeting [bold]{len(doc_folders)}[/bold] document(s): {[f'{f.parent.name}/{f.name}' for f in doc_folders]}\n")
    else:
        console.print(f"Found [bold]{len(doc_folders)}[/bold] document(s) in {input_dir}\n")

    page_set = set(page_stems) if page_stems else None
    if page_set:
        console.print(f"Limiting to page(s): [bold]{', '.join(sorted(page_set))}[/bold]\n")

    results = []
    for folder in doc_folders:
        path = transcribe_document(folder, output_dir, overwrite=overwrite, page_stems=page_set, skip_transcription=skip_transcription)
        results.append(path)

    if run_metadata:
        for folder in doc_folders:
            run_metadata_for_document(folder, output_dir, overwrite=overwrite, page_stems=page_set, hints=hints)
        combine_metadata_csvs(output_dir)

    return results
