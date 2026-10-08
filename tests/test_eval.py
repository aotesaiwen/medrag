import json

import httpx
import pytest

from eval.metrics import aggregate, question_metrics
from eval.run import evaluate, load_questions


def test_metrics_use_all_gold_and_dense_before_reranking():
    scores = question_metrics(["a", "b"], ["a", "b"], ["x", "b"])
    assert scores == {"recall@5": 0.5, "recall@50": 1.0, "MRR@5": 0.5}


def test_duplicates_do_not_inflate_recall_and_limits_apply():
    scores = question_metrics(["a", "a", "b"], ["x"] * 50 + ["b"], ["a"] * 5 + ["b"])
    assert scores == {"recall@5": 0.5, "recall@50": 0.0, "MRR@5": 1.0}


def test_missing_relevance_is_invalid():
    with pytest.raises(ValueError):
        question_metrics([], [], [])


def test_no_hit_has_zero_reciprocal_rank():
    assert question_metrics(["a"], [], ["b"])["MRR@5"] == 0


def test_aggregate_macro_average_and_type_breakdown():
    report = aggregate([
        {"id": "1", "query_type": "exact_ref", "metrics": question_metrics(["a"], ["a"], ["a"])},
        {"id": "2", "query_type": "conceptual", "metrics": question_metrics(["a"], [], [])},
    ])
    assert report["overall"]["recall@5"] == 0.5
    assert report["per_query_type"]["exact_ref"]["MRR@5"] == 1.0


def test_dataset_rejects_unknown_reference(tmp_path):
    path = tmp_path / "set.jsonl"
    path.write_text(json.dumps({"id": "a", "question": "问题", "query_type": "conceptual", "relevant_chunk_ids": ["missing"]}))
    with pytest.raises(ValueError, match="unknown chunk"):
        load_questions(path, {"valid"})


def test_http_evaluation_failure_does_not_become_a_score():
    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        evaluate([{"question": "问题"}], client)
