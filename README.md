# archival-htr

HTR pipeline for historical manuscripts. Uses a vision model (Gemini or Ollama) to transcribe scanned images, annotates extracted metadata, and indexes everything in ChromaDB for full-corpus semantic search.

## Quickstart

```bash
# 1. Copy and fill in your API key / backend settings
cp .env.example .env

# 2. Drop manuscript folders into data/input/ (see layout below)

# 3. Build the container
docker compose build

# 4. Transcribe + metadata + index in one step
docker compose run --rm archival-htr ingest

# 5. Search
docker compose run --rm archival-htr search "your query here" --results 10
```

## Input layout

Input is a **three-level hierarchy**: `data/input/{collection}/{document}/{images}`.

- **Collection folder** (`data/input/14/`) — groups related documents under one archival series.
- **Document folder** (`data/input/14/001/`) — one petition or multi-page item. The folder name becomes the document identifier.
- **Images** — all image files (`.jpg`, `.png`, `.tif`, `.tiff`, `.webp`, `.bmp`) inside the document folder are treated as pages, sorted by filename.
- **Optional context files** — a `.txt` or `.xml` (PAGE XML) file alongside the images is used as HTR context during transcription and copied to `output/imported/`.

```
data/input/
  14/                        # collection
    001/                     # document
      IMG001.jpg
      IMG002.jpg
      001.txt                # optional: existing transcript (used as context)
      001.xml                # optional: PAGE XML (used as context)
    002/
      IMG003.jpg
  393/
    012/
      14245707_0015.jpg
    013/
      14245707_0016.jpg
```

## Output layout

All output goes under `data/output/` (or `--output`):

```
data/output/
  imported/
    {collection}/
      {doc}.txt              # copy of any .txt found in input
      {doc}.xml              # copy of any .xml found in input
  transcribed/
    {collection}/
      {doc}.txt              # combined transcript, sections delimited by --- {page} ---
      {doc}/
        {page_stem}.txt      # per-page transcript
  metadata/
    {collection}/
      {doc}_{page}.csv       # per-page metadata row
      classification.csv     # job-application / military-service flags per document
    combined.csv             # full merged metadata table
  finalized/
    {collection}/
      {doc}/
        {page_stem}.txt      # manually reviewed / corrected transcripts
```

## Commands

| Command | Description |
|---|---|
| `transcribe` | Transcribe images → `.txt` (per page + combined), then annotate metadata |
| `metadata` | Re-run metadata annotation for all transcribed docs, then rebuild `combined.csv` |
| `combine-metadata` | Merge all per-page CSVs in `output/metadata/` into `combined.csv` |
| `index` | Chunk and embed `.txt` files into the ChromaDB vector store |
| `ingest` | `transcribe` + `index` in one step |
| `search` | Semantic search across the indexed corpus |
| `ask` | RAG-powered Q&A: retrieve relevant passages, then synthesise an answer |
| `query` | Load one document's full transcript into context and ask a question (no RAG) |
| `reclassify` | Classify transcripts for job-application type and military-service argument, then re-index |
| `audit` | List combined transcript files that contain pages with empty transcripts |
| `fill-metadata` | Propagate metadata from one page to all other pages of the same document |
| `serve` | Start the FastAPI web UI |

### Filtering and scope flags

Most processing commands accept these flags to limit scope:

- `--doc / -d` — process only the named document folder(s) (repeatable; accepts `doc`, `collection/doc`, or `collection`)
- `--page / -p` — process only specific page stems (repeatable)
- `--overwrite` — re-process already-done files instead of skipping

`transcribe` and `ingest` also accept:

- `--no-metadata` — skip metadata annotation after transcription
- `--imported-only` — copy existing imported transcripts without running the vision model
- `--backend` — override the backend for this run (`ollama` or `gemini`)
- `--hints` — path to a JSON hints file with field-specific examples for metadata annotation

`search` and `ask` accept:

- `--source / -s` — limit to one collection or document (e.g. `14` or `14/001`)
- `--job-application / --no-job-application` — filter by job-application flag
- `--military-service / --no-military-service` — filter by military-service flag

### Examples

```bash
# Transcribe one document only (Docker)
docker compose run --rm archival-htr transcribe --doc 001

# Transcribe one document in one collection
docker compose run --rm archival-htr transcribe --doc 14/001

# Re-transcribe specific pages of a document
docker compose run --rm archival-htr transcribe --doc 001 --page IMG001 --page IMG002 --overwrite

# Use existing imported transcripts without the vision model
docker compose run --rm archival-htr transcribe --imported-only

# Search with filters
docker compose run --rm archival-htr search "request for pension" --source 14 --job-application

# Ask a question about the whole corpus
docker compose run --rm archival-htr ask "Which petitioners mention military service in Flanders?"

# Ask about a single document's full transcript (no RAG chunking)
docker compose run --rm archival-htr query "Who signed this petition?" --source 14/001

# Reclassify all documents and re-index with updated flags
docker compose run --rm archival-htr reclassify

# Find documents with empty transcribed pages
docker compose run --rm archival-htr audit

# Propagate language and date from the first page to all other pages of a document
docker compose run --rm archival-htr fill-metadata -c 14 -d 001 --field language --field date_submission_writing

# Start the web UI
docker compose run --rm -p 8080:8080 archival-htr serve
```

## Docker + Ollama (connection refused)

If you run the app **in Docker** with `BACKEND=ollama`, the container cannot reach `localhost:11434` (that's the container itself). Point `OLLAMA_BASE_URL` at your host:

- **Windows / Mac (Docker Desktop):** `OLLAMA_BASE_URL=http://host.docker.internal:11434`
- **Linux:** `OLLAMA_BASE_URL=http://172.17.0.1:11434` or run with `network_mode: host` and keep `http://localhost:11434`

Alternatively, use the Gemini backend: set `BACKEND=gemini` and `GEMINI_API_KEY` in `.env` (no local server needed).

## Configuration

All settings via `.env` — see `.env.example`. Key options:

| Variable | Default | Description |
|---|---|---|
| `BACKEND` | `ollama` | `ollama` or `gemini` |
| `GEMINI_MODEL` | — | Gemini model tier, e.g. `gemini-2.0-flash` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `COLLECTION_NAME` | `manuscripts` | ChromaDB collection name |
| `HTR_PROMPT` | — | Override the transcription prompt |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | — | Tune RAG chunking |
