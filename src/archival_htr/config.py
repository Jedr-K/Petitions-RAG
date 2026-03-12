import os
from dotenv import load_dotenv

load_dotenv()

# --- Backend selection ---
# BACKEND=ollama  → fully local via Ollama (default)
# BACKEND=gemini  → Gemini API (requires GEMINI_API_KEY)
BACKEND = os.getenv("BACKEND", "ollama")

# --- Gemini settings (used when BACKEND=gemini) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")

# --- Ollama settings (used when BACKEND=ollama) ---
# When running in Docker, use host hostname so the container can reach Ollama on the host:
#   OLLAMA_BASE_URL=http://host.docker.internal:11434  (Windows/Mac Docker Desktop)
#   OLLAMA_BASE_URL=http://172.17.0.1:11434             (Linux, or use host network)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# --- Shared settings ---
# Defaults are project-relative so local runs work on Windows/Mac/Linux; override in .env or Docker to e.g. /data/input
DATA_INPUT_DIR = os.getenv("DATA_INPUT_DIR", "data/input")
DATA_OUTPUT_DIR = os.getenv("DATA_OUTPUT_DIR", "data/output")
CHROMA_DIR = os.getenv("CHROMA_DIR", "data/chroma")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))       # words per RAG chunk
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))  # word overlap between chunks

HTR_PROMPT = os.getenv(
    "HTR_PROMPT",
    (
        "You are an expert paleographer transcribing historical manuscripts. "
        "Transcribe the handwritten text in this image exactly as written, "
        "preserving original spelling, punctuation, and line breaks. "
        "If a word is illegible, mark it as [illegible]. "
        "Do not add commentary, headings, or explanations — output only the transcription."
    ),
)

HTR_IMPROVE_PROMPT = os.getenv(
    "HTR_IMPROVE_PROMPT",
    (
        "You are an expert paleographer. Below you are given: (1) an image of a historical manuscript page, "
        "(2) existing transcription(s) of the same or related material (e.g. from other tools or pages) in .txt format. "
        "And (3) existing PAGE/XML line text (e.g. from other tools or pages) in .xml format. "
        "Produce one improved transcription for THIS image only. Use the image as the primary source; "
        "use the additional files (txt and xml) to resolve ambiguities, fix obvious errors, and preserve consistent spelling. "
        "Preserve original spelling and line breaks where appropriate. If a word is illegible, mark as [illegible]. "
        "Output only the transcription for this image — no commentary or headings."
        "If the image seems to contain several pages on top of each other, transcribe the top page only."
    ),
)

def validate_config():
    if BACKEND == "gemini" and not GEMINI_API_KEY:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to your .env file, or set BACKEND=ollama for fully local inference."
        )
    if BACKEND not in ("gemini", "ollama"):
        raise EnvironmentError(f"Unknown BACKEND='{BACKEND}'. Must be 'gemini' or 'ollama'.")
