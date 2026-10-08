"""Macro-averaged retrieval metrics with duplicate-safe relevance accounting."""

from collections import defaultdict
from typing import Iterable

METRICS = ("recall@5", "recall@50", "MRR@5")


def question_metrics(relevant: Iterable[str], dense: list[str], reranked: list[str]) -> dict[str, float]:
    gold = set(relevant)
    if not gold:
        raise ValueError("Every evaluation question must have at least one relevant chunk")
    top5 = reranked[:5]
    reciprocal_rank = next((1.0 / (i + 1) for i, value in enumerate(top5) if value in gold), 0.0)
    return {
        "recall@5": len(gold.intersection(top5)) / len(gold),
        "recall@50": len(gold.intersection(dense[:50])) / len(gold),
        "MRR@5": reciprocal_rank,
    }


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Evaluation set must not be empty")

    def mean(group: list[dict]) -> dict:
        return {"count": len(group), **{
            key: sum(row["metrics"][key] for row in group) / len(group) for key in METRICS
        }}

    by_type: dict[str, list] = defaultdict(list)
    for row in rows:
        by_type[row["query_type"]].append(row)
    return {"overall": mean(rows), "per_query_type": {
        key: mean(value) for key, value in sorted(by_type.items())
    }, "per_question": rows}
