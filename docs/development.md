# Development and evaluation

[Back to README](../README.md)

Run commands from the repository root after [installation](setup.md), with `.venv` activated.

## Frontend development

```bash
npm --prefix frontend run dev
```

Vite prints its URL, normally `http://127.0.0.1:5173`, and proxies API requests to port 8000. Keep the backend running while developing. `npm --prefix frontend run preview` previews the production build with the same proxy. Change [frontend/vite.config.ts](../frontend/vite.config.ts) if the backend address differs.

To deploy local frontend changes to the app served by the API:

```bash
python scripts/services.py stop api
npm --prefix frontend run build
python scripts/services.py start api
```

The API inventories `frontend/dist` at startup, so restart it after rebuilding. Corpus changes use the ingestion workflow and do not require a frontend rebuild.

Fonts are configured in [frontend/public/fonts.css](../frontend/public/fonts.css). Departure Mono is included locally with its license. Anthropic font declarations use hosted assets, so the browser may make external font requests; Chinese text uses system-font fallbacks.

## README screenshot

Keep a current screenshot of the homepage near the top of the README. Capture the actual running application at `http://127.0.0.1:8000/` in a fresh browser session, before submitting a question. Use a desktop viewport at 90% browser zoom and wait for the page, web fonts, and introductory typewriter text to load fully.

Preserve the application's normal fonts, font sizes, layout, and appearance. Do not mock API responses, inject conversations, modify styles, or block font requests for the screenshot. Save it as `docs/images/homepage.png`, inspect it, and update the README image when visible frontend changes make it outdated.

## Tests

The Python and frontend unit tests use fake providers and do not require model downloads, GPU inference, or hosted-model calls:

```bash
python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

Browser checks require the built application served on port 8000 and Playwright's Chromium:

```bash
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:browser -- tests/integration.spec.ts tests/speech.spec.ts
```

These checks mock API responses and speech audio. The browser URL is set in [frontend/playwright.config.ts](../frontend/playwright.config.ts). Tracing, screenshots, and video recording are disabled.

Live checks are opt-in:

```bash
COURSE_RAG_LIVE=1 npm --prefix frontend run test:browser -- tests/live.spec.ts
COURSE_RAG_SPEECH_LIVE=1 npm --prefix frontend run test:browser -- tests/speech-live.spec.ts
```

The first calls the configured DeepSeek service and expects `data/raw/slides/BME2133_Fall2025_Lecture8.pdf` for its PDF check; adapt that fixture for another corpus. The second uses real local GPU speech synthesis with a synthetic answer and requires the speech worker. Both read the application key from configuration.

## Retrieval evaluation

With the corpus indexed and the API running:

```bash
python -m eval.run
python -m eval.run --set eval/eval_set.jsonl --output data/processed/eval_results.json
```

The included [eval/eval_set.jsonl](../eval/eval_set.jsonl) has three example questions for a smoke test. Use a larger labelled set to assess course coverage. Each line must contain a unique ID, a Chinese question, `query_type` (`exact_ref` or `conceptual`), and actual relevant chunk IDs:

```json
{"id":"gdpr-right-to-access","question":"GDPR 第15条第1款赋予个人哪些访问权？","query_type":"exact_ref","relevant_chunk_ids":["gdpr:art15:p1"]}
```

Use `data/processed/chunks.jsonl` to identify all sources a complete answer needs. The runner validates source IDs, queries authenticated `/retrieve`, and writes a report only if evaluation succeeds.

| Metric | Meaning |
| --- | --- |
| `recall@5` | Fraction of relevant chunks found in the reranked top five. |
| `recall@50` | Fraction found in the dense search's top 50. |
| `MRR@5` | Reciprocal rank of the first relevant result in the final five, or zero. |

Reports include averages across questions, averages by query type, and individual results. These metrics assess retrieval, not answer correctness or speech quality.
