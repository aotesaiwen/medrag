"""Evaluate the real HTTP retrieval path; never replaces service errors with zeros."""

import argparse
import json
from pathlib import Path

import httpx

from eval.metrics import aggregate, question_metrics
from rag.config import ROOT, Settings


def load_questions(path: Path, valid_chunk_ids: set[str] | None = None) -> list[dict]:
    questions = []
    ids = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids:
            raise ValueError(f"Line {line_number}: missing or duplicate question id")
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            raise ValueError(f"Line {line_number}: question must be nonempty")
        if item.get("query_type") not in {"exact_ref", "conceptual"}:
            raise ValueError(f"Line {line_number}: invalid query_type")
        relevant = item.get("relevant_chunk_ids")
        if not isinstance(relevant, list) or not relevant or not all(isinstance(x, str) and x for x in relevant):
            raise ValueError(f"Line {line_number}: relevant_chunk_ids must be nonempty strings")
        if valid_chunk_ids is not None and (missing := set(relevant) - valid_chunk_ids):
            raise ValueError(f"Line {line_number}: unknown chunk IDs: {sorted(missing)}")
        ids.add(item["id"])
        questions.append(item)
    if not questions:
        raise ValueError("Evaluation set must not be empty")
    return questions


def evaluate(questions: list[dict], client: httpx.Client) -> dict:
    rows = []
    for item in questions:
        response = client.post("/retrieve", json={"question": item["question"]})
        response.raise_for_status()
        result = response.json()
        dense = [hit["id"] for hit in result["dense"]]
        reranked = [hit["id"] for hit in result["reranked"]]
        rows.append({
            "id": item["id"], "query_type": item["query_type"],
            "relevant_chunk_ids": item["relevant_chunk_ids"],
            "dense_ids": dense, "reranked_ids": reranked,
            "metrics": question_metrics(item["relevant_chunk_ids"], dense, reranked),
        })
    return aggregate(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=ROOT / "eval/eval_set.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/eval_results.json")
    parser.add_argument("--url", default=None)
    args = parser.parse_args()
    settings = Settings.load()
    if not settings.api_key:
        parser.error("API_KEY is missing")
    if not settings.chunks_path.exists():
        parser.error("Build the corpus before running evaluation")
    known = {json.loads(line)["id"] for line in settings.chunks_path.read_text().splitlines() if line.strip()}
    questions = load_questions(args.set, known)
    with httpx.Client(
        base_url=args.url or f"http://{settings.api_host}:{settings.api_port}",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=settings.request_timeout, trust_env=False,
    ) as client:
        report = evaluate(questions, client)
    report["build_date"] = settings.build_date
    report["dataset"] = str(args.set.relative_to(ROOT) if args.set.is_relative_to(ROOT) else args.set)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"overall": report["overall"], "per_query_type": report["per_query_type"]}, indent=2))
    print(f"Per-question results: {args.output}")


if __name__ == "__main__":
    main()
