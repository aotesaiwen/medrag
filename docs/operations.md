# Operations

[Back to README](../README.md)

Run commands from the repository root with `.venv` activated.

## Services

```bash
python scripts/services.py start serving
python scripts/services.py status all
python scripts/services.py stop serving
```

The `serving` group includes Qdrant, embedding, reranker, and the API. Individual services accept the same `start`, `status`, and `stop` actions.

| Service name | Purpose | Default address |
| --- | --- | --- |
| `qdrant` | Vector storage | `127.0.0.1:6333` |
| `embedding` | Query and document embeddings | `127.0.0.1:8101` |
| `reranker` | Passage reranking | `127.0.0.1:8102` |
| `vlm` | Slide image descriptions during ingestion | `127.0.0.1:8103` |
| `speech` | Optional speech synthesis | `127.0.0.1:8104` |
| `api` | Browser app and HTTP API | `127.0.0.1:8000` |

GPU launches wait for model health checks and can take several minutes. Qdrant and the API start asynchronously. Check `/health` on the API for `ready` and detailed `retrieval` status; HTTP 200 indicates reachability, not full readiness. Model health endpoints use `/health`; Qdrant uses `/healthz`.

The launcher manages this project's recorded processes and container, and refuses ports occupied by other processes. Process records live in `run/`, logs in `logs/`, and Qdrant data in `data/qdrant/`. Stopping services preserves these data and downloaded models.

Run `python scripts/services.py stop all` to include optional speech and the ingestion VLM. If a foreground build is running, interrupt it first: stopping managed services does not cancel the build or its temporary MinerU server. Keep MinerU, the ingestion VLM, and the serving models in separate GPU phases.

After a reboot, run `start serving` again. Qdrant uses Docker's `unless-stopped` restart policy; the Python processes do not automatically restart. To run just the API in the foreground, use `python -m api.main` and stop it with Ctrl-C.

## Update the corpus

Add or replace PDFs in `data/raw/slides/` and run:

```bash
python scripts/build.py
```

The build stops the API and GPU services, processes legal text and slides, updates the index, and restarts serving. It also restarts speech if speech was running before the build. Unchanged parsing and image descriptions are reused. Only new or changed embedding inputs are embedded again; metadata-only changes update payloads.

Removing a PDF can leave stale points in Qdrant. A rebuild that needs to remove points stops before changing the index unless pruning is enabled. After the updated corpus has been assembled, inspect the changes:

```bash
python -m rag.index --dry-run
```

To apply an intended removal of the listed derived points, rerun the build with `python scripts/build.py --allow-prune`. You can also use `python -m rag.index --allow-prune` directly when the corpus is already assembled and the embedding and Qdrant services are ready, then restart the API. Pruning removes stale index entries; it does not delete original PDFs or parser caches.

Changing the embedding model identity, precision, or document input format re-embeds the corpus. Changing vector dimensions requires a new collection; the indexer does not replace an incompatible collection automatically.

### Inspect individual ingestion stages

Use these commands to diagnose a failed build. Run them sequentially, with other builds stopped:

```bash
python scripts/services.py stop api speech embedding reranker vlm
python -m ingest legal
python scripts/parser_stage.py
python scripts/services.py start vlm
```

The VLM start command waits for readiness. Read its pinned revision, then assemble the slides and corpus:

```bash
VLM_REVISION="$(python -c 'import json; print(json.load(open("config/models.lock.json"))["vlm"]["revision"])')"
python -m ingest build-slides --vlm-revision "$VLM_REVISION"
python scripts/services.py stop vlm
CHUNKS_PATH="$(python -c 'from rag.config import Settings; print(Settings.load().chunks_path)')"
python -m ingest assemble --tokenizer models/embedding --output "$CHUNKS_PATH"
python scripts/services.py start qdrant embedding reranker
```

Confirm that Qdrant's `/healthz` and both model `/health` endpoints respond before indexing:

```bash
python -m rag.index
python scripts/services.py start api
```

`build-slides` defaults to 16 concurrent image-description requests; use `--image-workers 1` to diagnose failures serially. The parser batch owns its temporary MinerU process and shuts it down on completion or Ctrl-C. Lower-level `parse-slides` options are available through `python -m ingest parse-slides --help`.

### Sources and generated files

The legal corpus covers all 99 GDPR articles and 173 recitals, plus HIPAA sections 160.101–160.105, 164.102–164.106, 164.302–164.318, and 164.500–164.534. The downloader uses official EU sources for GDPR and eCFR XML at `SOURCE_DATE` for HIPAA. Reserved legal units are excluded.

Each slide is a chunk, including blank slides. Legal units follow paragraph, recital, and definition boundaries. Chunks over 1,000 embedding-input tokens are reported without further splitting. Extracted formulas use MinerU's LaTeX, and generated image descriptions appear as `[Image: ...]`. Extraction and image descriptions can contain errors; compare questionable results with the original slide.

| Location | Contents |
| --- | --- |
| `data/raw/slides/` | Lecture PDFs supplied for this installation. |
| `data/raw/legal/` | Official legal source downloads and download metadata. |
| `data/cache/slides/` | PDF manifest, per-file parser outputs, and image-description caches. |
| `data/processed/legal.jsonl`, `slides.jsonl` | Legal and slide chunks. |
| `data/processed/chunks.jsonl` | Assembled corpus used by indexing and evaluation. |
| `data/processed/legal_coverage.json` | Legal-unit inventory and coverage checks. |
| `data/processed/oversized_chunks.json` | Chunks exceeding the reporting threshold. |
| `data/processed/build_report.json` | Build dates, corpus counts, and model pins. |
| `data/processed/eval_results.json` | Retrieval evaluation results. |

## Configuration

[rag/config.py](../rag/config.py) reads `.env` from the repository root. Exported environment variables take precedence. Restart affected services after changes. Settings paths are resolved against the repository root; lower-level `python -m ingest` path options are relative to the working directory.

| Settings | Purpose and defaults |
| --- | --- |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Hosted DeepSeek connection; all three are required for answers and follow-up rewriting. |
| `API_KEY` | Bearer key for protected endpoints; an empty value disables access. |
| `API_HOST`, `API_PORT` | Application address, default `127.0.0.1:8000`. |
| `QDRANT_URL`, `QDRANT_COLLECTION` | Vector database URL and collection, default `http://127.0.0.1:6333` and `course_rag`. |
| `EMBEDDING_URL`, `EMBEDDING_MODEL` | Embedding endpoint and served model; default `http://127.0.0.1:8101/v1` and `Qwen/Qwen3-Embedding-8B`. |
| `RERANKER_URL`, `RERANKER_MODEL` | Reranking endpoint and model; default `http://127.0.0.1:8102` and `Qwen/Qwen3-Reranker-8B`. |
| `VLM_URL`, `VLM_MODEL` | Image-description endpoint and model; default `http://127.0.0.1:8103/v1` and `Qwen/Qwen3.8-27B`. |
| `SPEECH_URL`, `SPEECH_TIMEOUT` | Speech worker URL and deadline; default `http://127.0.0.1:8104` and 600 seconds. |
| `REQUEST_TIMEOUT` | Retrieval, indexing, hosted-model, and evaluation timeout; default 180 seconds. |
| `EMBEDDING_BATCH_SIZE` | Number of new or changed inputs per embedding request; default 8. |
| `SOURCE_DATE`, `BUILD_DATE` | Source snapshot and build metadata dates; defaults `2026-10-01` and `2026-10-05`. |
| `CHUNKS_PATH` | Assembled corpus; default `data/processed/chunks.jsonl`. |
| `MODEL_LOCK_PATH` | Model lock used by runtime scripts; default `config/models.lock.json`. The downloader always reads that standard path. |

The launcher fixes Qdrant and model ports at 6333 and 8101–8104. Changing a URL setting changes where clients connect, not the port the launcher binds. Changing `API_PORT` also requires updating the frontend development proxy and browser-test URL when using those tools. Keep local model endpoints local to preserve the documented data flow.

Model repositories, revisions, precision, and paths are recorded in [config/models.lock.json](../config/models.lock.json). Qdrant's image digest and GPU launch settings are defined in [scripts/services.py](../scripts/services.py).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `401` | Use the running service's `API_KEY`; reconnect after a browser reload. |
| `503` from `/ask` | Check the local key and all three `LLM_*` settings. |
| `502` from a question endpoint | Inspect `/health` and the relevant service log in `logs/`. |
| Retrieval or index unavailable | Assemble and index the corpus with the pinned embedding identity. |
| Port occupied | Identify the existing process and choose an available port or stop the process if it is yours. |
| Model still loading | Check its health endpoint; a process record alone does not establish readiness. |
| Browser page missing or stale assets | Build the frontend and restart the API as described in the development guide. |
| Speech unavailable | Start the optional worker and check `http://127.0.0.1:8104/health`. |
