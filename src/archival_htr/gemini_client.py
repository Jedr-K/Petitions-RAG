"""
Unified model client — routes calls to either Gemini API or local Ollama
depending on the BACKEND environment variable.

Public API (identical regardless of backend):
    transcribe_image(image_path: Path) -> str
    embed_text(text: str) -> list[float]
    embed_query(query: str) -> list[float]
"""

import base64
import json
import urllib.request
from pathlib import Path

from archival_htr import config

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
