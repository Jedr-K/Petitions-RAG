"""
Unified LLM client — routes calls to either Gemini API or local Ollama
depending on the BACKEND environment variable.

Public API (identical regardless of backend):
    transcribe_image(image_path: Path, existing_context: str = "") -> str
    annotate_metadata(image_path: Path, transcript: str) -> DocumentMetadata
    embed_text(text: str) -> list[float]
    embed_query(query: str) -> list[float]
"""

import base64
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from archival_htr import config

# Metadata categories (Dutch) for document classification
METADATA_CATEGORIES = [
    "Petitie",
    "Sollicitatie",
    "Appostille/addendum",
    "Rapport",
    "Bijlage",
    "Attest",
    "Andere",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image_to_base64(image_path: Path) -> tuple[str, str]:
    """Return (base64_data, mime_type) for a local image file."""
    suffix = image_path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".tif": "image/tiff", ".tiff": "image/tiff",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return data, mime_type


def _ollama_post(endpoint: str, payload: dict) -> dict:
    """POST JSON to Ollama and return parsed response."""
    url = f"{config.OLLAMA_BASE_URL}{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def _build_prompt(existing_context: str) -> str:
    """Return the appropriate prompt depending on whether existing context is available."""
    if existing_context:
        return f"{config.HTR_IMPROVE_PROMPT}\n\nExisting transcription context:\n{existing_context}"
    return config.HTR_PROMPT


def _transcribe_gemini(image_path: Path, existing_context: str = "") -> str:
    import google.generativeai as genai
    from PIL import Image as PILImage

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    image = PILImage.open(image_path)
    response = model.generate_content([_build_prompt(existing_context), image])
    return response.text.strip()


def _transcribe_ollama(image_path: Path, existing_context: str = "") -> str:
    img_b64, _ = _image_to_base64(image_path)
    payload = {
        "model": config.OLLAMA_VISION_MODEL,
        "prompt": _build_prompt(existing_context),
        "images": [img_b64],
        "stream": False,
    }
    result = _ollama_post("/api/generate", payload)
    return result["response"].strip()


def transcribe_image(image_path: Path, existing_context: str = "") -> str:
    """Transcribe a single manuscript image page, optionally guided by existing context."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _transcribe_gemini(image_path, existing_context)
    return _transcribe_ollama(image_path, existing_context)


# ---------------------------------------------------------------------------
# Metadata annotation (image + transcript → structured metadata)
# ---------------------------------------------------------------------------

@dataclass
class DocumentMetadata:
    """Structured metadata inferred from image and HTR transcript."""
    language: str
    single_page_or_part: str  # "single_page" | "part_of_larger" or brief description
    related_to_others: str    # relation to other documents in corpus
    date_submission_writing: str
    category: str             # one of METADATA_CATEGORIES


def _metadata_prompt(transcript: str) -> str:
    categories_str = ", ".join(METADATA_CATEGORIES)
    return f"""You are an expert archivist analyzing a historical document. You are given:
1. An image of the document (manuscript/printed page).
2. A refined transcription (HTR) of the text on that page.

From the image and transcript, infer the following and respond with a single JSON object only (no markdown, no explanation):

- "language": Primary language of the source (e.g. Dutch, French, Latin). Use "unknown" if unclear.
- "single_page_or_part": Either "single_page" if this appears to be a complete standalone document, or "part_of_larger" with a brief note (e.g. "part_of_larger (continuation of petition)").
- "related_to_others": Brief note on how this document might relate to others in the same corpus (e.g. cover letter for a petition, attachment to a report). Use "unknown" if no clear relation.
- "date_submission_writing": Inferred date of submission or writing (year or range, e.g. "1789", "ca. 1790-1795"). Use "unknown" if not inferrable.
- "category": Exactly one of: {categories_str}

Transcription (for context):
---
{transcript[:8000]}
---

Respond with only the JSON object."""


def _parse_metadata_response(raw: str) -> DocumentMetadata:
    """Extract JSON from LLM response and return DocumentMetadata."""
    raw = raw.strip()
    # Strip markdown code block if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    category = data.get("category", "Andere")
    if category not in METADATA_CATEGORIES:
        category = "Andere"
    return DocumentMetadata(
        language=data.get("language", "unknown"),
        single_page_or_part=data.get("single_page_or_part", "unknown"),
        related_to_others=data.get("related_to_others", "unknown"),
        date_submission_writing=data.get("date_submission_writing", "unknown"),
        category=category,
    )


def _annotate_metadata_gemini(image_path: Path, transcript: str) -> DocumentMetadata:
    import google.generativeai as genai
    from PIL import Image as PILImage

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    image = PILImage.open(image_path)
    response = model.generate_content([_metadata_prompt(transcript), image])
    return _parse_metadata_response(response.text.strip())


def _annotate_metadata_ollama(image_path: Path, transcript: str) -> DocumentMetadata:
    img_b64, _ = _image_to_base64(image_path)
    payload = {
        "model": config.OLLAMA_VISION_MODEL,
        "prompt": _metadata_prompt(transcript),
        "images": [img_b64],
        "stream": False,
    }
    result = _ollama_post("/api/generate", payload)
    return _parse_metadata_response(result["response"].strip())


def annotate_metadata(image_path: Path, transcript: str) -> DocumentMetadata:
    """Generate annotated metadata from an image and its refined HTR transcript."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _annotate_metadata_gemini(image_path, transcript)
    return _annotate_metadata_ollama(image_path, transcript)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def _embed_gemini(text: str, task_type: str) -> list[float]:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    result = genai.embed_content(
        model=config.GEMINI_EMBEDDING_MODEL,
        content=text,
        task_type=task_type,
    )
    return result["embedding"]


def _embed_ollama(text: str) -> list[float]:
    payload = {"model": config.OLLAMA_EMBED_MODEL, "input": text}
    result = _ollama_post("/api/embed", payload)
    # Ollama returns {"embeddings": [[...floats...]]}
    return result["embeddings"][0]


def embed_text(text: str) -> list[float]:
    """Embed a document chunk for indexing."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _embed_gemini(text, "retrieval_document")
    return _embed_ollama(text)


def embed_query(query: str) -> list[float]:
    """Embed a search query."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _embed_gemini(query, "retrieval_query")
    return _embed_ollama(query)
