# Course RAG

A local retrieval service and browser frontend over the GDPR, selected HIPAA sections, and the lecture PDFs in `data/raw/slides/`. Ask in Chinese; source documents and citation labels are in English. `DECISIONS.md` is unchanged; the frontend was added at the owner's subsequent request.

With RAG on, dense search retrieves 50 chunks, the local reranker selects five, and DeepSeek writes the answer. DeepSeek also rewrites follow-up questions using the last five conversation turns. With RAG off, DeepSeek answers directly from the question and recent conversation; rewriting and retrieval are skipped. Thinking is explicitly disabled for all these calls in `rag/answer.py` with `extra_body={"thinking": {"type": "disabled"}}`. Questions, recent history, and any retrieved chunks are the only application data sent to a hosted model. PDF parsing, image descriptions, embeddings, reranking, and Qdrant storage run locally.

In both modes, answer generation receives the last five turns as alternating user and assistant messages, with citation markers removed. Follow-ups retain their context when RAG is toggled. The prompts instruct the model to resolve short references and corrections from the conversation, avoid repeating earlier misunderstandings, and ignore unrelated retrieved material. RAG answers drawn from model knowledge still carry the required label and no source citations. These instructions improve conversation handling but do not guarantee every model response.

## Verified build: 2026-10-05

The API is running on `127.0.0.1:8000`, with Qdrant, embedding, and reranking services running locally. MinerU and the ingestion VLM are stopped. The pre-existing `milvus-gui` container was stopped with the owner's explicit authorization to free port 8000.

The corpus contains **1,785 chunks**: 587 GDPR, 218 HIPAA, and 980 slides from 19 PDFs. The VLM produced 733 cached descriptions covering 752 image references. The oversized-chunk report lists 41 chunks; the largest has 4,781 embedding-input tokens. The first indexing run embedded all 1,785 chunks; a second real run reused all 1,785 with zero embeddings, metadata updates, or removals.

The **250 Python tests and frontend offline unit tests pass**. Live checks confirmed healthy dependencies and rejection of missing or invalid API keys. DeepSeek successfully answered a Chinese GDPR deletion-rights question and a follow-up through both the HTTP client and the integrated browser UI. Synthetic conversation checks also covered pronouns, short corrections, necessary clarification, and ignoring unrelated retrieval fixtures. Seventeen browser checks cover citation selection, original slide pages, RAG mode switching, passage quoting, history, retries, deletion during a pending request, blocked storage, theme, motion, and Markdown tables with scrolling and citations. The slide viewer was also checked against a real authenticated PDF page. Questions and answers are not saved to project files; the browser retains its own tab's conversations in session storage.

The three supplied example questions achieved these retrieval results:

| Group | Questions | Recall@5 | Recall@50 | MRR@5 |
| --- | ---: | ---: | ---: | ---: |
| Overall | 3 | 1.0 | 1.0 | 1.0 |
| Exact reference | 2 | 1.0 | 1.0 | 1.0 |
| Conceptual | 1 | 1.0 | 1.0 | 1.0 |

Each individual example also scored 1.0 on all three metrics. Full rankings are in `data/processed/eval_results.json`. This is a small smoke test; the owner's approximately 50-question evaluation set remains to be supplied for broader assessment. No credentials or slide inputs are missing.

## Browser frontend

Open **http://127.0.0.1:8000/** on the host. Click **connect** and enter `API_KEY` from the project's `.env`. This is the local service key. The browser keeps it only in memory, so reconnect after reloading. DeepSeek credentials remain on the backend. When working remotely, forward port 8000 over SSH to access the same loopback service.

The supplied two-column desktop layout (minimum 1120 × 740), pixel animation, fonts, theme, motion control, instructions, and tab-local conversation history are preserved. Answers and sources now come from `/ask`. Numbered citations select the matching legal passage or original slide page for that answer, independent of retrieval order. Only cited sources appear in the source rail. An answer with no citations has an explicit empty source state. The standalone rewritten question is available below a follow-up answer.

Answers render Markdown tables with column headers, cell formatting, and working source citations. Wide tables scroll horizontally inside the conversation pane. Recognizable tables whose row breaks were collapsed onto one line are repaired for display; ambiguous text and code remain untouched. This also applies to answers already saved in this browser tab.

The **RAG on / RAG off** switch sits inside each question box, including the follow-up and selected-passage composers. It defaults to on and can be changed at any time, even while an answer is pending; it applies when the next message is sent. Off answers use model knowledge and conversation history without retrieving new materials or adding source citations. Earlier cited answers remain available. Each message records its mode, and retry uses that original mode. The switch preference is saved in this browser tab along with the conversation; older saved sessions default to on. A passage you explicitly select is still included in your question in either mode.

Select text in a legal source to ask about that passage. Its text and source ID become explicit question context; the last five completed turns are sent with citation markers removed. A quoted source becomes a citation only if the backend retrieves and cites it in the new turn. Each answer retains its own numbering and sources. Legal links open the relevant official source.

Slide sources display the original PDF page inline with its figures and layout. The backend renders only the requested page locally as a PNG, in memory, without adding cached images to the workspace. Extracted text is still used for retrieval and answer generation. Reconnect after reloading to view protected slide pages; a failed preview can be retried and never falls back to transcribed text.

Pending answers can be cancelled or retried. Cancel stops the browser waiting; an already-running backend/model request may finish. Failed and cancelled turns are excluded from future question history. Deleting a pending conversation does not recreate it when its request finishes. Conversation deletion affects this browser tab only. If session storage is blocked or full, the current page remains usable in memory and displays a notice.

The API serves the production frontend directly, so `start api` / `stop api` controls both. No Node process is required while serving. To install or rebuild the UI, run from the project root with Node 22.12+ and npm:

```bash
npm --prefix frontend ci
.venv/bin/python scripts/services.py stop api
npm --prefix frontend run build
.venv/bin/python scripts/services.py start api
```

Build before restarting: the API registers an exact inventory of `frontend/dist` assets at startup. Only the page and built static assets allow public GET/HEAD requests. `/ask`, `/retrieve`, `GET /auth`, `GET /slides/{filename}`, `GET /slides/{filename}/pages/{page}`, and `POST /speech` require the same Bearer key. Page numbers start at 1; the page endpoint returns `image/png`. `GET /health` remains public. Original PDFs, slide previews, configuration files, source code, and the input ZIP are not public assets.

For frontend development, keep the backend running and use `npm --prefix frontend run dev`; stop it with Ctrl-C. Vite prints its URL, normally `http://127.0.0.1:5173`, and proxies API requests to `127.0.0.1:8000`. `npm --prefix frontend run preview` previews the build with the same proxy. Update `frontend/vite.config.ts` if developing against a different backend address. Changes to the PDF corpus use the normal ingestion workflow and do not require rebuilding the UI.

Only 18 source/build-input files were imported from `course-rag-frontend-handoff.zip`. Historical handoff/QA documents and 205 unused legacy font files were omitted. The original ZIP is retained. `frontend/public` is about 56 KB. Dependency and build directories are ignored by Git. Departure Mono and its license are included locally. The supplied Anthropic font declarations still reference their official hosted assets; Chinese uses the supplied system-font fallbacks. Source passages use the same fonts as the main page.

Frontend checks:

```bash
npm --prefix frontend test
npm --prefix frontend run build
# Browser checks require the running, built application on port 8000.
# Download only Playwright's browser when needed; no system-package installer is invoked.
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:browser -- tests/integration.spec.ts
# Explicit live check: two UI turns using the configured DeepSeek service.
COURSE_RAG_LIVE=1 npm --prefix frontend run test:browser -- tests/live.spec.ts
```

The standard browser checks mock model/API responses and use no hosted model. They verify slide-page selection, switching back to legal text, stale-request cancellation, reconnecting, and preview failures without showing a transcription. They also check on/off/on switching within a conversation, persistence after reload, shared quotation-composer state, changing modes while a request is pending, and retrying with the original mode. The opt-in live check privately reads `API_KEY` from `.env`, tests a question and quoted follow-up, and verifies an authenticated PDF download. Browser tracing, screenshots, and video recording are disabled for these checks.

## Local speech playback

Each completed answer has pixel speech icons at the right of its footer, beside **standalone question** when available. The speaker icon starts listening; pause, play/resume, stop, and cancel also use pixel icons with hover labels and accessible names. Status text retains the UI’s 14 px Departure Mono font. Synthesis starts only on a click; the whole passage plays after its audio is ready. Select text inside an answer to listen to just that passage. Citation buttons and Markdown syntax are omitted, while the displayed answer and its sources stay intact. Pause, resume, stop, cancel, and replay are available. Only one answer plays at a time; changing chats, deleting the active chat, disconnecting, or changing the key stops playback. Up to eight audio results, bounded to 64 MiB, are cached in page memory for replay and discarded when leaving the chat. Audio is not saved in session storage or project files.

The local worker uses **Fun-CosyVoice3-0.5B-2512**, its RL checkpoint, FP16 inference, the standard ten acoustic decoder steps, natural speaking speed, and native **24 kHz mono PCM WAV**. Its reference voice is the official runtime's `asset/zero_shot_prompt.wav`. Model weights, runtime source, its Matcha dependency, and Chinese/English normalization rules are pinned in `config/models.lock.json`. Rules are checksum-verified at startup. The worker preloads the voice and warms the model before accepting requests; it performs no downloads while serving. All speech text stays on this machine. The live browser check also confirmed playback of real model-generated WAV audio. A synthetic listening sample is saved at `data/processed/speech_demo.wav`; ordinary app requests never save audio to disk.

On this RTX PRO 6000 Blackwell with embedding and reranking still resident, two warm synthetic trials per length measured:

| Chinese passage length, including punctuation | Complete synthesis | Resulting speech |
| --- | --- | --- |
| 93 characters | 4.9–5.2 s | 17.7–19.0 s |
| 204 characters | 11.9–12.1 s | 41.0–42.3 s |

A separate live HTTP check measured 5.2 seconds for 93 characters and 29.7 seconds for a complete 501-character answer (103.2 seconds of audio), with retrieval still healthy. These are small timing samples, not a percentile guarantee or a listening-quality evaluation. **Ten seconds is the target for short passages; the owner chose to preserve full answers and allow longer synthesis for long passages.** Nothing is summarized, cut off, or sped up to fit ten seconds. After ten seconds the UI shows “Still preparing…” on one line beside the pixel cancel icon. Requests allow up to 6,000 input characters, a 64 MiB WAV response, and a default 600-second backend deadline. Cancellation stops the browser waiting; an already-running model computation can finish. Concurrent synthesis requests receive a retryable busy response instead of joining a queue.

The acceleration adapter in `speech/cuda_graph.py` replays the original Qwen decoder with a static KV cache, avoiding per-token Python GPU dispatch. It keeps the checkpoint, sampling algorithm, and acoustic renderer. Startup compares its prefill and decode hidden states with eager execution before enabling it. Sequences exceeding the static cache use the original dynamic decoder. The unaccelerated baseline took 8.7–10.4 seconds for 93 characters and 19.9–21.3 seconds for 204 characters. Reproduce timings with synthetic passages only:

```bash
.venv-tts/bin/python scripts/benchmark_speech.py
# Compare with the original decoder:
.venv-tts/bin/python scripts/benchmark_speech.py --eager --output data/processed/speech_benchmark_eager.json
```

The model runs in **`.venv-tts`**, independent of `.venv-gpu` and `.venv-parser`. Installation is already complete on this host. For a fresh installation:

```bash
uv venv --python 3.10.12 .venv-tts
uv pip install --python .venv-tts/bin/python -r requirements-speech.lock --index-strategy unsafe-best-match
git clone https://github.com/FunAudioLLM/CosyVoice.git models/CosyVoice-runtime
git -C models/CosyVoice-runtime checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C models/CosyVoice-runtime submodule update --init --recursive
.venv/bin/python scripts/download_models.py speech
.venv/bin/python scripts/download_speech_rules.py
```

Start and stop this optional service independently:

```bash
.venv/bin/python scripts/services.py start speech
.venv/bin/python scripts/services.py status speech
.venv/bin/python scripts/services.py stop speech
```

The worker binds `127.0.0.1:8104`; `GET /health` is public, and `POST /synthesize` requires the existing API key. Browsers call the authenticated **`POST /speech`** endpoint on port 8000. Both responses are `Cache-Control: no-store`, and neither service logs speech requests. `SPEECH_URL` and `SPEECH_TIMEOUT` configure the gateway; the provided worker launcher fixes port 8104. `start serving` retains its existing four-service behavior; run `start speech` as well when audio is wanted. `stop all` includes speech. The corpus builder stops speech before ingestion and restarts it afterward if it had been running. Cold startup and warmup are separate from per-request synthesis time.

Speech tests:

```bash
.venv/bin/python -m pytest tests/test_speech.py -q
npm --prefix frontend run test:browser -- tests/speech.spec.ts
# Actual GPU synthesis and browser playback; the answer fixture is synthetic.
COURSE_RAG_SPEECH_LIVE=1 npm --prefix frontend run test:browser -- tests/speech-live.spec.ts
```

The browser checks play a synthetic WAV and cover opt-in synthesis, citation omission, selection, pause/resume, replay without resynthesis, cancellation, navigation, and retry. Model quality still needs human listening on representative course answers, especially English abbreviations, numbers, and formulas.

## Prepare the environment

Run commands from the project root on the GPU host:

```bash
cd ~/projects/course_rag
uv sync --frozen --dev --python 3.10.12
source .venv/bin/activate
python --version
```

The application uses Python **3.10.12**; `uv.lock` pins its complete dependency graph. Docker must be usable by the current account, and host access must expose the RTX PRO 6000 Blackwell GPU to the model processes. GPU commands cannot run in a sandbox that hides the device. Installing system packages, changing drivers or system settings, or stopping another project's services requires the owner's approval.

The GPU serving and parser dependencies live in separate Python 3.10.12 environments. Their full dependency lists are pinned; install them sequentially to reuse the shared package cache:

```bash
uv venv --python 3.10.12 .venv-gpu
uv pip install --python .venv-gpu/bin/python -r requirements-gpu.lock
uv venv --python 3.10.12 .venv-parser
uv pip install --python .venv-parser/bin/python -r requirements-parser.lock
```

The serving environment uses vLLM 0.19.1, Transformers 5.18.0, and Torch 2.10.0. The parser uses MinerU 3.4.5, vLLM 0.19.1, Transformers 4.57.6, and Torch 2.10.0. MinerU's Transformers version is isolated from the serving environment so its parser-specific compatibility requirements do not change the serving model runtime. Keep `.venv` activated for the commands below; the scripts select `.venv-gpu` and `.venv-parser` where needed.

Keep the existing `.env`. For a fresh installation only, copy `.env.example` to `.env`, fill the three `LLM_*` settings, and generate the local API key:

```bash
python - <<'PY'
from pathlib import Path
import secrets
from dotenv import dotenv_values

path = Path('.env')
if dotenv_values(path).get('API_KEY'):
    raise SystemExit('An API_KEY already exists; it was kept.')
lines = path.read_text().splitlines()
lines = [line for line in lines if not line.startswith('API_KEY=')]
path.write_text('\n'.join([*lines, 'API_KEY=' + secrets.token_urlsafe(32)]) + '\n')
path.chmod(0o600)
print('Generated API_KEY in .env without printing it.')
PY
```

Without DeepSeek settings, first-turn retrieval and retrieval evaluation still work. Answer generation and history-dependent rewriting return a configuration error. Without `API_KEY`, protected endpoints reject every request.

Download model artifacts before starting GPU services:

```bash
python scripts/download_models.py
# Or download a subset:
python scripts/download_models.py embedding reranker
```

Downloads require network access. Once downloaded, model serving uses local paths and offline model loading. The downloads are large; preserve completed artifacts in `models/` between runs.

## Start, inspect, and stop services

The launcher manages only processes and the Qdrant container recorded as belonging to this project. It refuses occupied ports and does not stop their existing owners. Process records are in `run/`, logs in `logs/`, and persistent Qdrant data in `data/qdrant/`.

| Service | Start | Stop | Address |
| --- | --- | --- | --- |
| Qdrant | `python scripts/services.py start qdrant` | `python scripts/services.py stop qdrant` | `127.0.0.1:6333` |
| Embedding model | `python scripts/services.py start embedding` | `python scripts/services.py stop embedding` | `127.0.0.1:8101` |
| Reranker | `python scripts/services.py start reranker` | `python scripts/services.py stop reranker` | `127.0.0.1:8102` |
| Ingestion VLM | `python scripts/services.py start vlm` | `python scripts/services.py stop vlm` | `127.0.0.1:8103` |
| HTTP API | `python scripts/services.py start api` | `python scripts/services.py stop api` | `API_HOST:API_PORT` |

For normal question answering, run Qdrant, embedding, reranker, and the API:

```bash
python scripts/services.py start serving
python scripts/services.py status all
```

GPU start commands wait for each model's health check before launching the next model, which can take several minutes. This keeps GPU memory profiling from overlapping. A model startup failure aborts the remaining launch sequence. Qdrant and the HTTP API start asynchronously; check readiness before indexing or querying:

```bash
curl --fail --silent http://127.0.0.1:8101/health
curl --fail --silent http://127.0.0.1:8102/health
API_PORT="$(python -c 'from rag.config import Settings; print(Settings.load().api_port)')"
curl --silent "http://127.0.0.1:${API_PORT}/health"
```

`GET /health` is public. Its HTTP response proves that the API is reachable; inspect `ready` and `retrieval` in the JSON for model, Qdrant, and index readiness. `ready` also requires the DeepSeek settings and API key. A live process or HTTP 200 alone does not mean retrieval is ready.

Stop the normal serving stack, or all five managed services in the table:

```bash
python scripts/services.py stop serving
python scripts/services.py stop all
```

Stopping services keeps model files, cached parsing, and Qdrant data. Qdrant uses Docker's `unless-stopped` restart policy; the Python service processes are detached but do not automatically restart after a host reboot. Run `start serving` after reboot. For a foreground API process, `python -m api.main` runs the same application; stop that foreground process with Ctrl-C.

If a build or parser batch is running in another terminal, interrupt that foreground command first. `services.py stop all` controls the managed services, including speech; it does not cancel the build command or its temporary MinerU batch server.

The specification's default API port is **8000**. If the owner chooses a different API port, set `API_PORT` in `.env` and restart this project's API. The CLI and eval runner read that setting automatically.

The supplied launcher fixes Qdrant and model ports at 6333 and 8101–8103. Changing their URL settings changes client destinations; it does not change those launch ports.

## Build the corpus and add slide PDFs

Place each lecture's PDF in `data/raw/slides/`, using a filename containing its lecture number, such as `Lecture 6.pdf`. Lecture numbers must be unique. They determine source IDs and the English `Lecture 6` citation heading; no metadata file is needed. If there are no slide PDFs, the build stops.

After the environments and model downloads are ready, run the complete build on the host:

```bash
python scripts/build.py
```

This temporarily stops this project's API and GPU serving models. It fetches/parses legal text, runs MinerU, runs the VLM for image descriptions, assembles the corpus and oversized-chunk report, starts embedding and reranking, updates Qdrant, and starts the API. A successful build leaves the normal serving stack running. It never stops a foreign process to acquire its GPU or port.

The GPU stages are deliberately separate:

1. MinerU's local hybrid backend parses slides while the serving models and Qwen image-description VLM are stopped. One temporary loopback API process serves the whole batch and exits when parsing finishes, while cache entries remain separate for every PDF.
2. Qwen3.8-27B runs alone to describe the extracted slide images, with sixteen requests in flight. Descriptions are cached once per unique image and assembled in the original slide order. The build then stops it. Add `--image-workers 1` to the `python -m ingest build-slides` command below to make this stage serial when troubleshooting.
3. Qwen3-Embedding-8B and Qwen3-Reranker-8B share the GPU for indexing and question answering.

To add or replace PDFs, copy them into `data/raw/slides/` and rerun `python scripts/build.py`. Unchanged PDF parsing is reused. A changed PDF or a changed parser/model configuration invalidates that PDF's parse cache; unchanged image descriptions are reused when their image, prompt, and VLM revision agree. Indexing embeds only new or changed embedding inputs. Metadata-only edits update payloads without another embedding request.

When the owner deliberately removes a PDF, the rebuilt corpus omits its slides while retaining old parser caches. Removing the resulting stale Qdrant points is separately guarded to respect the owner's data-deletion rule. Preview the index update after assembly:

```bash
python -m rag.index --dry-run
```

After the owner authorizes removal of the listed stale derived points, use `python -m rag.index --allow-prune`, or `python scripts/build.py --allow-prune` for the full rebuild. Without that flag, an index update that needs pruning exits before modifying the index. No command here deletes original PDFs or wipes the collection.

All chunks are embedded again only when the pinned embedding model identity, precision, or document input format changes. A change in vector dimensions requires a new collection; the indexer preserves the old collection rather than silently recreating it.

### Run individual ingestion stages

These commands are useful when inspecting a failed build. They are sequential: do not overlap MinerU, the VLM, and serving models.

```bash
python scripts/services.py stop api embedding reranker vlm
python -m ingest legal --date 2026-10-01
python scripts/parser_stage.py
python scripts/services.py start vlm
```

Wait until `curl --fail http://127.0.0.1:8103/health` succeeds, then continue:

```bash
python -m ingest build-slides \
  --vlm-base-url http://127.0.0.1:8103/v1 \
  --vlm-model Qwen/Qwen3.8-27B \
  --vlm-revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --date 2026-10-01
python scripts/services.py stop vlm
CHUNKS_PATH="$(python -c 'from rag.config import Settings; print(Settings.load().chunks_path)')"
python -m ingest assemble --tokenizer models/embedding --output "${CHUNKS_PATH}"
python scripts/services.py start qdrant embedding reranker
```

Wait for both model health endpoints, then run:

```bash
python -m rag.index
python scripts/services.py start api
```

For a running foreground MinerU stage, Ctrl-C interrupts that job and shuts down its temporary API process. The low-level `python -m ingest parse-slides --help` command also supports a separately managed loopback server through `--mineru-api-url`; the supplied batch script owns that lifecycle automatically. The VLM is only needed for ingestion; leave it stopped while serving questions.

### Source and output files

| Location | Contents |
| --- | --- |
| `data/raw/slides/` | Owner-supplied lecture PDFs. |
| `data/raw/legal/` | Downloaded official GDPR HTML and dated eCFR XML. |
| `data/raw/legal/downloads.json` | Official source URLs and the selected download locations. |
| `data/cache/slides/manifest.json` | Current PDF-to-parser-output mapping. |
| `data/cache/slides/parsed/` | Per-file MinerU outputs, including extracted images and LaTeX. |
| `data/cache/slides/descriptions/` | Cached local VLM image descriptions. |
| `data/processed/legal.jsonl` | GDPR and selected HIPAA chunks. |
| `data/processed/legal_coverage.json` | Parsed legal-unit inventory and coverage checks. |
| `data/processed/slides.jsonl` | One chunk per slide, including empty slides. |
| `data/processed/chunks.jsonl` | Assembled corpus consumed by indexing. |
| `data/processed/oversized_chunks.json` | Chunks exceeding 1,000 embedding-input tokens; they are reported without further splitting. |
| `data/processed/eval_results.json` | Successful retrieval evaluation report. |
| `data/processed/build_report.json` | Configured build date, source date, corpus counts, and model pins. |

GDPR includes all 99 articles and 173 recitals. The canonical source is [EUR-Lex, CELEX 32016R0679](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679). When its automated-access challenge prevents retrieval, the downloader uses the same official English XHTML from the [EU Publications Office](https://op.europa.eu/o/opportal-service/download-handler?identifier=3e485e15-11bd-11e6-ba9a-01aa75ed71a1&format=xhtml&language=en&productionSystem=cellar&part=). It validates the article and recital inventory before accepting the document.

HIPAA uses official eCFR XML pinned to `SOURCE_DATE`, covering 45 CFR §§160.101–160.105, 164.102–164.106, 164.302–164.318, and 164.500–164.534. Reserved units are excluded. Legal chunks follow their paragraphs, recitals, or specified definition terms, with HIPAA introductions repeated as specified. Slides preserve MinerU's formulas and add image descriptions as `[Image: ...]`.

Slide extraction retains the parser's output. An audit found fragmented diagram labels on Lecture 15, slide 22; descriptions of cropped images cannot repair text outside those crops. The VLM also miscounted the word-cloud panels on Lecture 3, slide 7 (six instead of eight). Generated image descriptions can therefore contain errors. Retrieval quality depends on extraction quality and the owner's labelled evaluation set.

## Ask questions

With `.venv` activated and services ready:

```bash
python -m api.ask "GDPR 第17条规定，在什么情况下可以要求删除个人数据？"
python -m api.ask --json "HIPAA 允许患者要求更正健康信息吗？"
python -m api.chat
```

The chat CLI retains only the most recent five turns in memory. `/clear` starts a new conversation and `/exit` quits. Neither the HTTP service nor the CLI saves chat history to disk. Earlier answers keep the citations printed with their own turn; each new answer starts its citation numbering at `[1]`.

To call the HTTP API with curl, read the key from configuration without printing it:

```bash
API_KEY="$(python -c 'from rag.config import Settings; print(Settings.load().api_key)')"
API_PORT="$(python -c 'from rag.config import Settings; print(Settings.load().api_port)')"
curl --fail-with-body --silent --show-error \
  "http://127.0.0.1:${API_PORT}/ask" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"question":"GDPR 的删除权是什么？","history":[]}'
```

`POST /ask` accepts:

```json
{
  "question": "它有哪些例外？",
  "rag_enabled": true,
  "history": [
    {
      "question": "GDPR 的删除权是什么？",
      "answer": "上一轮返回的中文答案。"
    }
  ]
}
```

Pass previous questions and answers in chronological order. The server takes the last five complete turns and removes citation markers before sending history to DeepSeek. `rag_enabled` is an optional JSON boolean that defaults to `true`. Set it to `false` to skip question rewriting, embedding, vector search, and reranking and call DeepSeek once with the question and recent history. Direct answers require the hosted model settings but do not require the retrieval services to be available.

The response contains `question`, `rewritten_question`, `answer`, `citations`, `sources`, and `rag_enabled`. With RAG on, each citation includes its number, chunk ID, and English label. Up to five returned sources include their text, heading path, metadata, and reranking score. With RAG off, `rewritten_question` is the original question and both `citations` and `sources` are empty.

`POST /retrieve` accepts `question` and `history` and always performs retrieval, returning `question`, `rewritten_question`, the pre-reranking `dense` list, and the final `reranked` list. Omit history for retrieval that does not use DeepSeek. It requires the same Bearer authentication as `/ask`.

The answer model cites chunk IDs internally. The server removes IDs outside the current five sources and converts valid IDs to numbered citations. Ordinary slide numbers in brackets are not interpreted as source IDs. GDPR articles supply legal authority; recitals supply supporting interpretation. Material the retrieved sources do not support is separately labelled **模型自身知识（非所提供资料）** and receives no source citation.

## Evaluation

The checked-in `eval/eval_set.jsonl` contains **three example questions**, not the owner's intended approximately 50-question evaluation set. With the corpus indexed and the HTTP service running:

```bash
python -m eval.run
python -m eval.run --set eval/eval_set.jsonl --output data/processed/eval_results.json
```

The runner uses the authenticated `/retrieve` path. It checks that every relevant chunk ID exists in the current corpus, fails on service errors, and writes its report only after evaluation succeeds. A failed service call is never converted into an artificial zero score.

The report contains overall means, means per query type, and individual question results:

| Metric | Meaning |
| --- | --- |
| `recall@5` | Fraction of labelled relevant chunks found in the reranked top five. |
| `recall@50` | Fraction of labelled relevant chunks found in dense search's top 50, before reranking. |
| `MRR@5` | Reciprocal rank of the first relevant result in the final five, or zero if none appears. |

Add one JSON object per line to the eval file, or create another `.jsonl` file and pass `--set`. Each ID must be unique. `query_type` must be `exact_ref` or `conceptual`, and `relevant_chunk_ids` must contain one or more actual IDs:

```json
{"id":"gdpr-right-to-access","question":"GDPR 第15条第1款赋予个人哪些访问权？","query_type":"exact_ref","relevant_chunk_ids":["gdpr:art15:p1"]}
```

Inspect `data/processed/chunks.jsonl` to label the expected sources. Include all chunks that a complete answer needs. Metrics are macro-averaged across questions; the three examples are a smoke test and do not establish broad retrieval quality.

## Configuration reference

`rag.config.Settings` reads `.env` in the project root. Exported environment variables override `.env`; the build, service, indexing, eval, and question commands use this loader. Restart a running service after changing its settings. Settings paths are resolved against the project root.

The lower-level `python -m ingest` commands read `.env` for `SOURCE_DATE`, `VLM_URL`, and `VLM_MODEL`, and accept their own path options relative to the working directory. In particular, pass `--output` to `assemble` when customizing `CHUNKS_PATH`, as the manual example above does. `scripts/build.py` supplies that output setting automatically.

| Setting | Purpose |
| --- | --- |
| `LLM_BASE_URL` | Owner's DeepSeek OpenAI-compatible API base URL. Empty by default; required for answers and follow-up rewrites. |
| `LLM_MODEL` | DeepSeek model name sent to its API. Empty by default; answer and rewrite temperature is fixed at zero, with thinking explicitly disabled. |
| `LLM_API_KEY` | Owner's hosted DeepSeek credential. Empty by default; keep it private. |
| `API_KEY` | Generated secret accepted by this project's `Authorization: Bearer` authentication. Empty by default, which disables access to protected routes. |
| `API_HOST` | Local HTTP bind/client address, default `127.0.0.1`; keep that address for the specified local service. |
| `API_PORT` | Local HTTP port, default `8000`. Change only to the owner's selected available port. |
| `QDRANT_URL` | Local Qdrant HTTP URL, normally `http://127.0.0.1:6333`. |
| `QDRANT_COLLECTION` | Collection holding this project's vectors and chunk payloads, default `course_rag`. Existing foreign data is rejected. |
| `EMBEDDING_URL` | Local embedding OpenAI API base, normally `http://127.0.0.1:8101/v1`. |
| `EMBEDDING_MODEL` | Served embedding model name, default `Qwen/Qwen3-Embedding-8B`; must agree with the pinned model lock. |
| `RERANKER_URL` | Local vLLM reranker base URL, normally `http://127.0.0.1:8102`; its `/score` endpoint is used. |
| `RERANKER_MODEL` | Served reranker model name, default `Qwen/Qwen3-Reranker-8B`. |
| `VLM_URL` | Local image-description OpenAI API base, normally `http://127.0.0.1:8103/v1`; ingestion only. |
| `VLM_MODEL` | Served image-description model name, default `Qwen/Qwen3.8-27B`. |
| `REQUEST_TIMEOUT` | Retrieval, indexing, DeepSeek, and eval request timeout in seconds, default `180`. The question HTTP client allows three times this value. Ingestion image descriptions have a separate fixed 240-second timeout. |
| `EMBEDDING_BATCH_SIZE` | Number of new or changed chunks embedded per indexing request, default `8`; must be positive. |
| `SOURCE_DATE` | Fixed legal source snapshot date, default `2026-10-01`. Used for the eCFR snapshot and recorded in legal and slide chunk metadata. |
| `BUILD_DATE` | Fixed date recorded in generated build/evaluation metadata, default `2026-10-05`, avoiding wall-clock timestamps in deterministic outputs. |
| `CHUNKS_PATH` | Assembled JSONL corpus read by indexing and eval, normally `data/processed/chunks.jsonl`. |
| `MODEL_LOCK_PATH` | Path to model repository, commit, precision, and local-path configuration; normally `config/models.lock.json`. The downloader reads that standard lock path. |

Do not point local model URLs at a hosted service: the privacy design assumes only DeepSeek answer/rewrite calls leave the machine.

## Model pins and retrieval details

The committed model lock pins the official repositories to these revisions:

| Role | Repository | Commit |
| --- | --- | --- |
| Embedding | `Qwen/Qwen3-Embedding-8B` | `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af` |
| Reranking | `Qwen/Qwen3-Reranker-8B` | `77d193c791ed757ca307ee72715aa132723da912` |
| Image descriptions | `Qwen/Qwen3.8-27B` | `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |
| PDF parsing | `opendatalab/MinerU2.5-Pro-2605-1.2B` | `bff20d4ae2bf202df9f45284b4d43681555a97ed` |
| MinerU layout/OCR/formula support | `opendatalab/PDF-Extract-Kit-1.0` | `ed6b654c018d742e65a17671e379c5e6ecc87ec9` |

Model serving uses BF16, with the same embedding precision at index and query time. Qdrant is pinned as `qdrant/qdrant@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`.

The application lock currently resolves FastAPI 0.142.2, HTTPX 0.28.1, OpenAI client 2.54.0, Uvicorn 0.54.0, PyMuPDF 1.28.2, and pytest 9.1.1. `uv.lock` is the authoritative full application dependency list; GPU runtime versions are isolated as described above. The auxiliary MinerU pipeline models retain their configured native precision.

Queries use the official Qwen format:

```text
Instruct: Given a question, retrieve the regulation text or lecture slide content that answers it
Query:用户的独立问题
```

Document inputs are heading-path lines followed by document text, without a query instruction. The reranker uses the same English task through `config/qwen3_reranker.jinja`. There is no BM25, overlapping window splitting, hidden document truncation, LangChain, LlamaIndex, or Haystack.

## Tests and troubleshooting

```bash
python -m pytest -q
```

Tests use fake providers and in-memory HTTP transports; they require neither model downloads, GPU inference, live DeepSeek credentials, nor network access. In the restricted agent sandbox, FastAPI's test transport can hang because its thread wakeup sockets are blocked. Run the same offline suite on the host in that environment.

- **`401` from the API:** use the same `.env` API key as the running service and include the Bearer header.
- **`503` from `/ask`:** inspect whether DeepSeek's three settings and the local API key are filled in.
- **`502` from a question endpoint:** inspect `/health` and the relevant model log. The API intentionally returns a generic upstream error without echoing question text or credentials.
- **No matching index:** assemble and index the corpus with the pinned embedding identity. A collection made with a different model revision or precision is excluded from search.
- **Port already occupied:** identify the existing service and obtain its owner's approval before stopping it. The launcher leaves it running.
- **Model still loading:** `status` reports processes; use model health endpoints to confirm readiness.

The frontend uses the existing API key; there are no user accounts, visual retrieval, or model-comparison features. Access logs and vLLM request logs are disabled. Browser conversations stay in tab-local session storage and do not become a server-side conversation database.
