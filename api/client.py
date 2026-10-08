"""Shared HTTP client and human-readable answer display."""

from typing import Any

import httpx

from rag.answer import HistoryTurn, normalize_history
from rag.config import Settings


def ask(question: str, history: list[HistoryTurn] | None = None,
        settings: Settings | None = None) -> dict[str, Any]:
    config = settings or Settings.load()
    if not config.api_key:
        raise RuntimeError("Set API_KEY in .env before asking questions.")
    turns = normalize_history(history or [])
    try:
        response = httpx.post(
            f"http://{config.api_host}:{config.api_port}/ask",
            headers={"Authorization": f"Bearer {config.api_key}"},
            json={"question": question,
                  "history": [{"question": t.question, "answer": t.answer} for t in turns]},
            timeout=config.request_timeout * 3,
            trust_env=False,
        )
    except httpx.HTTPError:
        raise RuntimeError("Cannot reach the local API. Check its status and .env settings.") from None
    if response.is_error:
        raise RuntimeError(f"API request failed (HTTP {response.status_code}). Check service status.")
    return response.json()


def display(result: dict[str, Any]) -> str:
    lines = []
    if result["rewritten_question"] != result["question"]:
        lines.append(f"检索问题：{result['rewritten_question']}\n")
    lines.append(result["answer"])
    if result["citations"]:
        lines.append("\nSources:")
        lines.extend(f"[{c['number']}] {c['label']} ({c['chunk_id']})"
                     for c in result["citations"])
    return "\n".join(lines)
