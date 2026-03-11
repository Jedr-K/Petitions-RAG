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

# 4. Transcribe + metadata + index in one step
docker compose run archival-htr ingest

# 5. Search
docker compose run archival-htr search "your query here" --results 10
```

## Commands

| Command | Description |
|---|---|
| `transcribe` | Image → .txt via vision model; then metadata annotation + combine |
| `metadata` | Run metadata (image + transcript → CSV) for all transcribed docs, then combine |
| `combine-metadata` | Merge all single-line metadata CSVs in `output/metadata/` into `combined.csv` |
| `index` | .txt → ChromaDB vector store |
| `ingest` | transcribe + metadata + index in one step |
| `search` | Query the corpus |

Use `--doc` / `-d` on `transcribe`, `ingest`, or `metadata` to limit to named subfolders. Use `--no-metadata` on `transcribe` or `ingest` to skip metadata annotation and combine.

```bash
# Single-document test (Docker)
docker compose run --rm archival-htr transcribe --doc 14245707_0001_113628200

# Or with plain docker
docker run --rm -v "$(pwd)/data:/data" --env-file .env archival-htr transcribe --doc 14245707_0001_113628200
```

## Input and output

**Input:** Collection folders. Each subfolder of `data/input/` is one collection (one multi-page document). Inside a collection:

- **Root:** page images (`.jpg`, `.png`, etc.) and optional metadata (e.g. `metadata.xml`, `mets.xml`). Only image files are used as pages; processing order is sorted by filename.
- **Subfolders:** `txt/` and `page/` hold per-page transcripts and PAGE XML. File names share the same stem as the image: e.g. image `14245707_0003_113628074.jpg` → `txt/14245707_0003_113628074.txt`, `page/14245707_0003_113628074.xml`. These are used as HTR context for that page and copied to `output/imported/{doc_name}/`.

Naming convention: stems can be `collectionID__readableId__objectID` (two underscores). The same stem links image, `.txt`, and `.xml` across root, `txt/`, and `page/`.

If there are no subfolders in `data/input/`, the input dir itself is treated as one collection (flat layout).

```
data/input/
  393/                              # collection folder (doc name = 393)
    metadata.xml
    mets.xml
    14245707_0003_113628074.jpg     # images in root
    14245707_0004_113628075.jpg
    ...
    page/                           # PAGE XML per page (same stem as image)
      14245707_0003_113628074.xml
      14245707_0004_113628075.xml
    txt/                            # transcript per page (same stem as image)
      14245707_0003_113628074.txt
      14245707_0004_113628075.txt
```

**Output** (under `data/output/` or `--output`):

- `imported/{doc_name}/` — copied `txt/` and `page/` from the collection
- `transcribed/` — one concatenated .txt per document plus per-page files: `transcribed/{doc_name}/{page_stem}.txt` (one per image); optional `transcribed/{doc_name}/page/` with PAGE XML
- `metadata/` — one single-line CSV per analysed (image, transcript) pair; `metadata/combined.csv` is the merged table (language, category, date, etc.). Categories (Dutch): Petitie, Sollicitatie, Appostille/addendum, Rapport, Bijlage, Attest, Andere. To add a single image: use `ingest.annotate_and_write_metadata(image_path, transcript, output_dir, doc_name, page_id)`, then run `combine-metadata`.

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
