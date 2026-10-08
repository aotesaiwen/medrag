# DECISIONS.md

Version 1.0 · Last updated 2026-10-05

Self-hosted RAG over GDPR, selected HIPAA sections, and about 20 slide decks. Questions and answers are in Chinese, documents in English, citation labels in English. Anything not covered here: use the most common, well-established practice, keep going, and list the choice in your final summary.

## Corpus

- GDPR from EUR-Lex HTML: all 99 articles and 173 recitals.
- HIPAA from eCFR XML: 45 CFR sections 160.101 to 160.105, 164.102 to 164.106, 164.302 to 164.318, and 164.500 to 164.534.
- Slides: PDF files in the project folder's `data/raw/slides/` directory, one per lecture. No metadata file is provided. Each lecture's name is "Lecture" plus the lecture number in its file name, e.g. "Lecture 6". This name is used in citations and as the heading of each of its slides.

## Chunking

- One chunk per legal unit: GDPR article paragraph, recital, HIPAA top-level paragraph. A whole article or section becomes one chunk only if it has no paragraphs. Text is never stored twice: no overlapping chunks, and an article or section that has paragraphs gets no chunk of its own. The repeated HIPAA introductions below are the one deliberate exception.
- HIPAA: a section's introductory text is repeated at the start of each of its paragraph chunks.
- Split further only in two places: GDPR Article 4, one chunk per point (`gdpr:art4:5`), and the HIPAA definitions sections 160.103, 164.103, 164.304, and 164.501, one chunk per defined term (`hipaa:160.103:business-associate`).
- No other splitting. Chunks over 1,000 tokens are listed in a report. Reserved units get no chunk.
- Slides: one chunk per slide, all kept, a blank slide has empty text. Images are described by the VLM and inserted as `[Image: ...]`; formulas use MinerU's LaTeX.
- IDs: `gdpr:art17:p1`, `gdpr:rec65`, `hipaa:164.526.a`, `slides:lecture6:s7` (lecture 6, slide 7). Definition term slugs: lowercase, non-alphanumeric runs become one hyphen.

## Models and services

- Parser: MinerU, model MinerU2.5-Pro-2605-1.2B, hybrid backend. Local.
- VLM: Qwen3.8-27B, Qwen's official full-precision (BF16) release `Qwen/Qwen3.8-27B`. Local, served by vLLM, used only during ingestion.
- Embedding: Qwen3-Embedding-8B, Qwen's official full-precision (BF16) weights; indexing and querying always use the same precision. Local, served by vLLM. Each question is embedded in Qwen's official query format, `Instruct: {task}\nQuery:{question}`, with the task written in English as Qwen recommends: "Given a question, retrieve the regulation text or lecture slide content that answers it". Documents get no instruction, as in Qwen's examples; each is embedded as its heading path followed by its text.
- Reranker: Qwen3-Reranker-8B, Qwen's official full-precision (BF16) weights. Local, served by vLLM. Uses the same English task description as the embedding.
- LLM: DeepSeek, through its hosted API with the `openai` client, temperature 0. API. This is the only step that sends data off the machine: each question, the conversation history, and the 5 retrieved chunks go to DeepSeek.
- Vector database: Qdrant. Local, in Docker.
- Every local model is pinned by commit hash.
- Plain Python 3.10.12 with uv; FastAPI. No LangChain, LlamaIndex, or Haystack.

## Pipeline

- Retrieval: take the top 50 results from dense search, rerank them, and keep the top 5. No BM25 keyword search: it only matches identical words, and Chinese questions share no words with the English documents.
- Answers: Chinese. The LLM cites sources by chunk ID, e.g. `[gdpr:art17:p1]`, never by number, so bracketed numbers copied from the slides can't be mistaken for citations. Every cited ID is checked against the 5 retrieved chunks and removed if it isn't one of them; valid ones are shown to the user as numbered citations. GDPR articles are the legal basis; recitals only support. If the 5 retrieved chunks don't contain the answer, or cover only part of it, answer the rest from the LLM's own knowledge, clearly marked as not coming from the provided materials and without citations.
- Multi-turn conversations: each request carries the conversation history (previous questions and answers, the last 5 turns, with citation markers removed); the service stores nothing. Before retrieval, the LLM rewrites the latest question into a standalone question using the history; the first turn skips this. Retrieval uses the rewritten question, and the answer is generated from the history, the question, and the 5 retrieved chunks. Each answer cites only chunks retrieved in its own turn, its citation numbers start at [1], and earlier answers keep their citations unchanged. The response includes the rewritten question.
- Our own HTTP service, running locally on 127.0.0.1:8000: `POST /ask`, `POST /retrieve`, `GET /health`. Command line: `python -m api.ask "<question>"` for one question, and `python -m api.chat` for a multi-turn session whose history lives only in memory. Every request except `GET /health` must carry an API key as `Authorization: Bearer <key>`; keys come from config, and requests without a valid key are rejected. No question logging.

## Eval

- `eval/eval_set.jsonl`: about 50 Chinese questions written by the owner, each with `id`, `question`, `query_type` (`exact_ref` or `conceptual`), and `relevant_chunk_ids`. A 3-question example set ships with the code.
- Metrics: recall@5, recall@50 (before reranking), MRR@5. Reported overall, per query type, and per question.

## Machine

- GPU: RTX PRO 6000 Blackwell, 96 GB, used only by this project.
- GPU schedule: during ingestion, MinerU runs first, then the VLM alone. During serving, the embedding model and reranker share the GPU.
- The agent runs every step itself, including the GPU steps. Its sandbox can't see the GPU, so GPU steps must run with host access.
- Builds are deterministic: times and versions come from config, so rebuilding from the same sources gives the same chunks.
- Updates are incremental: parsing results are cached per file, so only new or changed files are parsed again, and a deleted file's chunks simply disappear. Indexing embeds only new or changed chunks and removes chunks that no longer exist. All chunks are embedded again only when the embedding model, its precision, or the embedding input format changes.

## Not doing

- BM25 keyword search
- Question logging

## TODO (don't implement yet)

- A UI (not designed yet) and user login for it
- Visual retrieval for slides
- Model comparisons, once the eval set exists
