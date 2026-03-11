# archival-htr

HTR pipeline for historical manuscripts. Uses Gemini Vision to transcribe scanned images and ChromaDB for full-corpus search.

## Quickstart

```bash
# 1. Copy and fill in your API key
cp .env.example .env

# 2. Drop manuscript folders into data/input/
#    Structure: data/input/manuscript_001/page_01.jpg, page_02.jpg ...

# 3. Build the container
docker compose build

# 4. Transcribe + index in one step
docker compose run archival-htr ingest

# 5. Search
docker compose run archival-htr search "your query here" --results 10
```

## Commands

| Command | Description |
|---|---|
| `transcribe` | Image → .txt via Gemini Vision |
| `index` | .txt → ChromaDB vector store |
| `ingest` | transcribe + index in one step |
| `search` | Query the corpus |

Use `--doc` / `-d` on `transcribe` or `ingest` to process only named subfolders; omit to process all.

```bash
# Single-document test (Docker)
docker compose run --rm archival-htr transcribe --doc 14245707_0001_113628200

# Or with plain docker
docker run --rm -v "$(pwd)/data:/data" --env-file .env archival-htr transcribe --doc 14245707_0001_113628200
```

## Input convention

One subfolder per document, images named in page order:

```
data/input/
  manuscript_001/
    page_01.jpg
    page_02.jpg
  manuscript_002/
    page_01.png
    ...
```

## Docker + Ollama (Connection refused)

If you run the app **in Docker** with `BACKEND=ollama`, the container cannot reach `localhost:11434` (that’s the container itself). Point `OLLAMA_BASE_URL` at your host:

- **Windows / Mac (Docker Desktop):** In `.env` set  
  `OLLAMA_BASE_URL=http://host.docker.internal:11434`
- **Linux:** Use `http://172.17.0.1:11434` or run the container with `network_mode: host` and keep `http://localhost:11434`.

Alternatively use the Gemini backend: set `BACKEND=gemini` and `GEMINI_API_KEY` in `.env` (no local server needed).

## Configuration

All settings via `.env` — see `.env.example`. Key options:

- `GEMINI_MODEL` — swap model tier without code changes (e.g. `gemini-2.0-flash`)
- `HTR_PROMPT` — override the transcription prompt for different document types
- `CHUNK_SIZE` / `CHUNK_OVERLAP` — tune RAG chunking
