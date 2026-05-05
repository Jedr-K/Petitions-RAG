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
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from archival_htr import config

# Controlled vocabulary constants for metadata normalisation
METADATA_CATEGORIES = ["petition", "apostille", "attachement", "Other"]

PETITION_TYPES = [
    "Request for financial aid",
    "Request for permission",
    "Request for certification",
    "Job application",
    "Complaint",
    "other",
]

SCOPE_VALUES  = ["single document", "part of dossier"]
GENDER_VALUES = ["Male", "Female", "unknown"]

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


_ollama_validated = False


def check_ollama() -> None:
    """Verify Ollama is reachable and the configured models are available.

    Raises ConnectionError if Ollama is not running, or EnvironmentError if a
    required model has not been pulled yet.
    """
    global _ollama_validated
    if _ollama_validated:
        return
    try:
        req = urllib.request.Request(f"{config.OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Cannot reach Ollama at {config.OLLAMA_BASE_URL}. "
            "Is Ollama running? For Docker Desktop set "
            "OLLAMA_BASE_URL=http://host.docker.internal:11434 in your .env file. "
            f"Original error: {e}"
        ) from e

    available = {m["name"].split(":")[0] for m in data.get("models", [])}
    for model_name in (config.OLLAMA_VISION_MODEL, config.OLLAMA_EMBED_MODEL):
        base = model_name.split(":")[0]
        if base not in available:
            raise EnvironmentError(
                f"Ollama model '{model_name}' is not available. "
                f"Pull it first with:  ollama pull {model_name}"
            )
    _ollama_validated = True


def _ollama_post(endpoint: str, payload: dict) -> dict:
    """POST JSON to Ollama and return parsed response."""
    check_ollama()
    url = f"{config.OLLAMA_BASE_URL}{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Ollama request to {url} failed: {e}"
        ) from e


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
# Text-only query (context + question → answer)
# ---------------------------------------------------------------------------

def _query_gemini(context: str, query: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    prompt = f"""You are an expert in historical manuscripts. Below is a full transcription (or set of transcriptions) from a single collection.

Use only the provided text to answer the question. Quote or cite the relevant passages when possible. If the answer is not in the text, say so.

---
TRANSCRIPTION:
{context}
---

Question: {query}"""
    response = model.generate_content(prompt)
    return response.text.strip()


def _query_ollama(context: str, query: str) -> str:
    prompt = f"""You are an expert in historical manuscripts. Below is a full transcription (or set of transcriptions) from a single collection.

Use only the provided text to answer the question. Quote or cite the relevant passages when possible. If the answer is not in the text, say so.

---
TRANSCRIPTION:
{context}
---

Question: {query}"""
    payload = {
        "model": config.OLLAMA_VISION_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    result = _ollama_post("/api/generate", payload)
    return result["response"].strip()


def query_with_context(context: str, query: str) -> str:
    """Answer a question using the given transcript text as context. No image."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _query_gemini(context, query)
    return _query_ollama(context, query)


# ---------------------------------------------------------------------------
# Document classification (transcript → job application + military service flags)
# ---------------------------------------------------------------------------

def _classification_prompt(transcript: str) -> str:
    return f"""You are an expert archivist analysing a historical Dutch-language document transcription.

Based ONLY on the transcript below, answer two questions and respond with a single JSON object only (no markdown, no explanation):

- "is_job_application": true if this document is a petition or request explicitly seeking a specific position, office, job, or appointment. false if it is a general petition, report, attachment, or other document type.
- "military_service_argument": true if prior military service — of the petitioner or a family member — is cited as a supporting argument or qualification for the request. false otherwise.
- "reasoning": one sentence in English explaining your classification decision.

Transcript:
---
{transcript[:12000]}
---

Respond with only the JSON object."""


def _parse_classification_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"is_job_application": False, "military_service_argument": False, "reasoning": "parse error"}
    return {
        "is_job_application": bool(data.get("is_job_application", False)),
        "military_service_argument": bool(data.get("military_service_argument", False)),
        "reasoning": str(data.get("reasoning", "")),
    }


def _classify_gemini(transcript: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    response = model.generate_content(_classification_prompt(transcript))
    return _parse_classification_response(response.text.strip())


def _classify_ollama(transcript: str) -> dict:
    payload = {
        "model": config.OLLAMA_VISION_MODEL,
        "prompt": _classification_prompt(transcript),
        "stream": False,
    }
    result = _ollama_post("/api/generate", payload)
    return _parse_classification_response(result["response"].strip())


def classify_document(transcript: str) -> dict:
    """Classify a document from its transcript text only (no image required).

    Returns a dict with keys:
        is_job_application (bool)
        military_service_argument (bool)
        reasoning (str)
    """
    config.validate_config()
    if config.BACKEND == "gemini":
        return _classify_gemini(transcript)
    return _classify_ollama(transcript)


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
    is_job_application: bool = False          # petition explicitly requesting a position/office
    military_service_argument: bool = False   # prior military service cited as a qualification
    construction_works: bool = False          # document relates to construction or building works
    petitioner_name: str = "unknown"
    petitioner_gender: str = "unknown"
    petitioner_occupation: str = "unknown"
    petitioner_residence: str = "unknown"
    petitioner_birthplace: str = "unknown"
    petitioner_age: str = "unknown"
    petitioner_writing_for: str = "unknown"
    petition_type: str = "other"


def normalize_metadata(meta: "DocumentMetadata") -> "DocumentMetadata":
    """Clamp all controlled-vocabulary fields to their allowed sets."""
    # scope
    raw = (meta.single_page_or_part or "").lower()
    if "dossier" in raw or "part" in raw:
        meta.single_page_or_part = "part of dossier"
    else:
        meta.single_page_or_part = "single document"

    # relation (depends on normalised scope)
    if meta.single_page_or_part == "single document":
        meta.related_to_others = "standalone"
    else:
        rel = (meta.related_to_others or "").strip()
        if not rel.lower().startswith("attached to"):
            meta.related_to_others = "attached to " + rel if rel else "attached to "

    # category — map Dutch originals and variants to English targets
    _CAT_MAP = {
        "petitie": "petition",         "petition": "petition",
        "appostille/addendum": "apostille", "appostille": "apostille",
        "addendum": "apostille",       "apostille": "apostille",
        "rapport": "attachement",      "bijlage": "attachement",
        "attachement": "attachement",  "attachment": "attachement",
        "sollicitatie": "Other",       "attest": "Other",
        "andere": "Other",             "other": "Other",
    }
    meta.category = _CAT_MAP.get((meta.category or "").strip().lower(), "Other")

    # gender (1800s — infer from name/marital status only)
    g = (meta.petitioner_gender or "").strip().lower()
    if g in ("man", "male", "m"):
        meta.petitioner_gender = "Male"
    elif g in ("vrouw", "female", "f", "v", "woman"):
        meta.petitioner_gender = "Female"
    else:
        meta.petitioner_gender = "unknown"

    # petition_type
    _PT = {v.lower(): v for v in PETITION_TYPES}
    meta.petition_type = _PT.get((meta.petition_type or "").strip().lower(), "other")

    return meta


def _metadata_prompt(transcript: str) -> str:
    petition_types_str = ", ".join(f'"{v}"' for v in PETITION_TYPES)
    return f"""You are an expert archivist analyzing a 19th-century historical document. You are given:
1. An image of the document (manuscript/printed page).
2. A refined transcription (HTR) of the text on that page.

From the image and transcript, infer the following and respond with a single JSON object only (no markdown, no explanation):

- "language": Primary language of the source (e.g. Dutch, French, Latin). Use "unknown" if unclear.
- "single_page_or_part": Exactly one of: "single document" (complete standalone document) or "part of dossier" (continuation, cover letter, attachment, or similar).
- "related_to_others": If "single document", write "standalone". If "part of dossier", write "attached to <brief description>", e.g. "attached to petition of Jan de Vries".
- "date_submission_writing": Inferred date of submission or writing (year or range, e.g. "1789", "ca. 1790-1795"). Use "unknown" if not inferrable.
- "category": Exactly one of: petition, apostille, attachement, Other
- "petition_type": Exactly one of: {petition_types_str}. Choose the option that best describes the nature of the request.
- "is_job_application": true if this document is a petition or request explicitly seeking a specific position, office, job, or appointment. false otherwise.
- "military_service_argument": true if prior military service — of the petitioner or a family member — is cited as a supporting argument or qualification for the request. false otherwise.
- "construction_works": true if the document relates to construction, building works, repairs, infrastructure, or public works projects. This includes requests for reimbursement or subsidies for facade replacements. false otherwise.

## About the petitioner/author
- "petitioner_name": Full name of the petitioner as stated in the document. "unknown" if not mentioned.
- "petitioner_gender": Exactly one of: "Male", "Female", "unknown". Base this only on the name and marital status as written in the document (e.g. "weduwe" → Female, "de heer" → Male).
- "petitioner_occupation": Occupation or profession of the petitioner. "unknown" if not stated.
- "petitioner_residence": Place of residence of the petitioner. "unknown" if not stated.
- "petitioner_birthplace": Place of birth of the petitioner. "unknown" if not stated.
- "petitioner_age": Age of the petitioner (number or range, e.g. "34", "ca. 40"). "unknown" if not stated.
- "petitioner_writing_for": "own name" if writing in their own name; otherwise a brief description of who they represent (e.g. "widow of Jan de Vries", "on behalf of the community of X"). "unknown" if unclear.

Transcription (for context):
---
{transcript[:8000]}
---

Respond with only the JSON object."""


def enhance_metadata_prompt(transcript: str) -> str:
    categories_str = ", ".join(METADATA_CATEGORIES)
    petition_types_str = ", ".join(PETITION_TYPES)
    return f"""Using the provided .txt file containing the transcript and the .csv file containing the current metadata,
    enhance the csv string by reading the transcript and deducting if this
    1) is a complete text: use exactly "single document" or "part of dossier"
    2) what type of document this is. Use one of the following categories: {categories_str}
    3) the petition type. Use one of: {petition_types_str}
    4) the date of submission or writing
    Return the enhanced csv string."""


def _parse_metadata_response(raw: str) -> DocumentMetadata:
    """Extract JSON from LLM response and return DocumentMetadata."""
    raw = raw.strip()
    # Strip markdown code block if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Return safe defaults rather than crashing when LLM returns non-JSON
        return normalize_metadata(DocumentMetadata(
            language="unknown",
            single_page_or_part="unknown",
            related_to_others="unknown",
            date_submission_writing="unknown",
            category="Other",
        ))
    meta = DocumentMetadata(
        language=data.get("language", "unknown"),
        single_page_or_part=data.get("single_page_or_part", "unknown"),
        related_to_others=data.get("related_to_others", "unknown"),
        date_submission_writing=data.get("date_submission_writing", "unknown"),
        category=data.get("category", "Other"),
        is_job_application=bool(data.get("is_job_application", False)),
        military_service_argument=bool(data.get("military_service_argument", False)),
        construction_works=bool(data.get("construction_works", False)),
        petitioner_name=data.get("petitioner_name", "unknown"),
        petitioner_gender=data.get("petitioner_gender", "unknown"),
        petitioner_occupation=data.get("petitioner_occupation", "unknown"),
        petitioner_residence=data.get("petitioner_residence", "unknown"),
        petitioner_birthplace=data.get("petitioner_birthplace", "unknown"),
        petitioner_age=data.get("petitioner_age", "unknown"),
        petitioner_writing_for=data.get("petitioner_writing_for", "unknown"),
        petition_type=data.get("petition_type", "other"),
    )
    return normalize_metadata(meta)


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

def _enhance_metadata_gemini(transcript: str, metadata: str) -> DocumentMetadata:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    response = model.generate_content([enhance_metadata_prompt(transcript), metadata])
    return _parse_metadata_response(response.text.strip())

def _enhance_metadata_ollama(transcript: str, metadata: str) -> DocumentMetadata:
    payload = {
        "model": config.OLLAMA_VISION_MODEL,
        "prompt": enhance_metadata_prompt(transcript),
        "input": metadata,
    }
    result = _ollama_post("/api/generate", payload)
    return _parse_metadata_response(result["response"].strip()) 

def annotate_metadata(image_path: Path, transcript: str) -> DocumentMetadata:
    """Generate annotated metadata from an image and its refined HTR transcript."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _annotate_metadata_gemini(image_path, transcript)
    return _annotate_metadata_ollama(image_path, transcript)

def enhance_metadata(transcript: str, metadata: str) -> DocumentMetadata:
    """Enhance the metadata by reading the transcript and the current metadata."""
    config.validate_config()
    if config.BACKEND == "gemini":
        return _enhance_metadata_gemini(transcript, metadata)
    return _enhance_metadata_ollama(transcript, metadata)

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
