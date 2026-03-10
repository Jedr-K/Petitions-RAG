import re
from pathlib import Path
import shutil
from tqdm import tqdm
from rich.console import Console
from archival_htr import config
from archival_htr.gemini_client import transcribe_image

console = Console()

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

IMPORTED_SUBDIR = "imported"
TRANSCRIBED_SUBDIR = "transcribed"


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
    text = xml_path.read_text(encoding="utf-8")
    # PAGE uses namespaces; match Unicode content (may span lines)
    parts = re.findall(r"<Unicode>([^<]*)</Unicode>", text, re.DOTALL)
    return "\n".join(p.strip() for p in parts if p.strip())


def get_document_folders(input_dir: Path) -> list[Path]:
    """
    Each subfolder in input_dir is treated as one multi-page document.
    Falls back to treating root-level images as a single document named '_root'.
    """
    folders = sorted([f for f in input_dir.iterdir() if f.is_dir()])
    if not folders:
        # flat layout — treat whole input dir as one document
        folders = [input_dir]
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


def transcribe_document(doc_folder: Path, output_dir: Path, overwrite: bool = False) -> Path:
    """
    Copy imported .txt/.xml to output_dir/imported/; transcribe with Gemini (using existing
    material as context) and save to output_dir/gemini/{doc_name}.txt. Returns path to Gemini .txt.
    """
    doc_name = doc_folder.name
    imported_dir = output_dir / IMPORTED_SUBDIR
    gemini_dir = output_dir / TRANSCRIBED_SUBDIR
    gemini_txt_path = gemini_dir / f"{doc_name}.txt"

    _copy_imported_to(doc_folder, doc_name, imported_dir)

    if gemini_txt_path.exists() and not overwrite:
        console.print(f"[yellow]Skipping[/yellow] {doc_name} (already transcribed, use --overwrite to redo)")
        return gemini_txt_path

    pages = get_pages(doc_folder)
    if not pages:
        console.print(f"[red]No images found[/red] in {doc_folder}")
        return gemini_txt_path

    existing_context = _build_existing_context(doc_folder)
    if existing_context:
        console.print(f"[bold cyan]Transcribing[/bold cyan] {doc_name} ({len(pages)} pages) [with existing .txt/.xml context]")
    else:
        console.print(f"[bold cyan]Transcribing[/bold cyan] {doc_name} ({len(pages)} pages)")

    full_text_parts = []
    for page in tqdm(pages, desc=doc_name, unit="page"):
        try:
            text = transcribe_image(page, existing_context=existing_context)
            full_text_parts.append(f"--- {page.name} ---\n{text}")
        except Exception as e:
            console.print(f"[red]Error on {page.name}:[/red] {e}")
            full_text_parts.append(f"--- {page.name} ---\n[transcription error: {e}]")

    gemini_dir.mkdir(parents=True, exist_ok=True)
    gemini_txt_path.write_text("\n\n".join(full_text_parts), encoding="utf-8")
    console.print(f"[green]Saved[/green] → {gemini_txt_path}")

    xml_src = get_xml_path(doc_folder)
    if xml_src is not None:
        shutil.copy2(xml_src, gemini_dir / f"{doc_name}.xml")
        console.print(f"[green]Copied[/green] → {gemini_dir / f'{doc_name}.xml'}")

    return gemini_txt_path


def ingest_all(
    input_dir: Path = None,
    output_dir: Path = None,
    overwrite: bool = False,
    doc_names: list[str] | None = None,
) -> list[Path]:
    input_dir = input_dir or Path(config.DATA_INPUT_DIR)
    output_dir = output_dir or Path(config.DATA_OUTPUT_DIR)

    doc_folders = get_document_folders(input_dir)
    if doc_names:
        doc_folders = [f for f in doc_folders if f.name in doc_names]
        if not doc_folders:
            console.print(f"[red]No matching document(s) for --doc {doc_names}[/red]")
            return []
        console.print(f"Targeting [bold]{len(doc_folders)}[/bold] document(s): {[f.name for f in doc_folders]}\n")
    else:
        console.print(f"Found [bold]{len(doc_folders)}[/bold] document(s) in {input_dir}\n")

    results = []
    for folder in doc_folders:
        path = transcribe_document(folder, output_dir, overwrite=overwrite)
        results.append(path)

    return results
