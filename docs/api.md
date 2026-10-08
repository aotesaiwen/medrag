# HTTP API

[Back to README](../README.md)

The default base URL is `http://127.0.0.1:8000`. Protected endpoints require `Authorization: Bearer <API_KEY>`, using the local key from `.env`. The browser page, inventoried build assets, and `GET /health` are public. API schema and Swagger pages are disabled.

## Ask a question

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

`question` is required and must contain non-whitespace text, up to 20,000 characters. `history` defaults to an empty list; pass previous questions and answers in chronological order. The server uses the last five turns and removes citation markers before sending them to the model. `rag_enabled` is a JSON boolean and defaults to `true`.

With RAG enabled, follow-up questions are rewritten for retrieval, dense search returns up to 50 candidates, and the reranker selects up to five sources. With RAG disabled, rewriting and retrieval are skipped; the question and history go directly to DeepSeek. Answer and rewriting calls use temperature zero with thinking disabled.

The response contains:

| Field | Contents |
| --- | --- |
| `question` | Submitted question. |
| `rewritten_question` | Standalone retrieval question; the original question when no rewrite is needed. |
| `answer` | Chinese answer with numbered citations where applicable. |
| `citations` | Cited source references, each with `number`, `chunk_id`, and `label`. |
| `sources` | Retrieved sources with text, heading paths, metadata, and reranking scores. |
| `rag_enabled` | The mode used for this answer. |

With RAG disabled, `citations` and `sources` are empty. A direct answer needs the hosted-model settings but does not require the retrieval services.

The model cites chunk IDs internally. The backend removes IDs outside the current retrieved sources and converts valid IDs to numbered citations. Each answer starts its own numbering at `[1]`. Material drawn from model knowledge is instructed to carry the label **模型自身知识（非所提供资料）** without source citations. A quoted passage becomes a citation only if that source is retrieved and cited in the new answer.

From the repository root with `.venv` activated, this example reads the key without printing it:

```bash
API_KEY="$(python -c 'from rag.config import Settings; print(Settings.load().api_key)')"
API_URL="$(python -c 'from rag.config import Settings; s = Settings.load(); print(f"http://{s.api_host}:{s.api_port}")')"
curl --fail-with-body --silent --show-error \
  "$API_URL/ask" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"question":"GDPR 的删除权是什么？","history":[]}'
```

## Other endpoints

| Endpoint | Behavior |
| --- | --- |
| `POST /retrieve` | Accepts `question` and `history`; returns `question`, `rewritten_question`, the pre-reranking `dense` list, and final `reranked` list. Always performs retrieval. Omit history to avoid hosted-model rewriting. |
| `GET /auth` | Returns `{"ok": true}` for a valid application key. |
| `GET /slides/{filename}` | Returns the original PDF from the configured slides directory. |
| `GET /slides/{filename}/pages/{page}` | Returns an original PDF page rendered as `image/png`. Page numbers start at 1. Rendering happens in memory. |
| `POST /speech` | Accepts `{"text":"要朗读的文字。"}` and returns `audio/wav`; requires the optional local speech worker. See the speech guide for limits. |
| `GET /health` | Public readiness report containing `status`, `ready`, `llm_configured`, and `retrieval`. |

Slide and speech responses use `Cache-Control: no-store`. The server does not persist chat history, rendered slide images, or generated speech. The client supplies conversation history with each request.

## Errors

- `401`: missing or invalid Bearer key.
- `422`: invalid request, such as a blank question or an invalid page number.
- `502`: a required upstream service failed.
- `503`: configuration is incomplete or a required speech worker is unavailable.
- `429` or `504` from speech: synthesis is busy or timed out.

`GET /health` can return HTTP 200 with `"ready": false`; inspect the JSON before treating the full RAG stack as available. Its readiness check includes retrieval, so it can be degraded even when direct answers are usable.
