import json
from dataclasses import replace

import httpx
import pytest

from rag.config import TASK, Settings
from rag.retrieval import Embedder, Retriever, embedding_identity, point_payload, query_input
from rag.types import Chunk, Hit


@pytest.fixture
def settings(tmp_path):
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"embedding": {
        "repo_id": "Qwen/Qwen3-Embedding-8B", "revision": "a" * 40, "dtype": "bfloat16",
    }}))
    return Settings(model_lock_path=lock)


def test_official_query_format_and_documents_have_no_instruction():
    formatted = query_input("删除权有什么例外？")
    assert formatted.startswith(f"Instruct: {TASK}\nQuery:")
    assert formatted.endswith("删除权有什么例外？")
    chunk = Chunk("gdpr:art17:p3", "Exceptions to erasure.", ["GDPR", "Article 17", "Paragraph 3"])
    assert chunk.embedding_text.splitlines() == ["GDPR", "Article 17", "Paragraph 3", "Exceptions to erasure."]
    assert "Instruct:" not in chunk.embedding_text


def test_dense_fifty_is_reranked_to_five(settings):
    chunks = [Chunk(f"gdpr:art{i}:p1", f"Legal unit {i}", ["GDPR", f"Article {i}"]) for i in range(1, 51)]
    identity = embedding_identity(settings)
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path == "/v1/embeddings":
            assert body["input"][0].endswith("Query:个人有哪些权利？")
            assert "truncate_prompt_tokens" not in body
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.2, 0.8]}]})
        if request.url.path.endswith("/points/query"):
            assert body["limit"] == 50
            assert body["filter"]["must"][1]["match"]["value"] == identity.signature
            return httpx.Response(200, json={"result": {"points": [
                {"payload": point_payload(chunk, identity), "score": 1 - i / 100}
                for i, chunk in enumerate(chunks)
            ]}})
        if request.url.path == "/score":
            assert body["text_1"] == "个人有哪些权利？"
            assert len(body["text_2"]) == 50
            assert "truncate_prompt_tokens" not in body
            return httpx.Response(200, json={"data": [
                {"index": i, "score": i / 100} for i in reversed(range(50))
            ]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = Retriever(settings, client=client).retrieve("个人有哪些权利？")
    assert len(result.dense) == 50
    assert result.dense[0].chunk.id == "gdpr:art1:p1"
    assert [hit.chunk.id for hit in result.reranked] == [f"gdpr:art{i}:p1" for i in range(50, 45, -1)]
    assert calls[0] == "/v1/embeddings" and calls[-1] == "/score"


def test_rerank_ties_preserve_dense_order(settings):
    hits = [Hit(Chunk(f"gdpr:rec{i}", "Text", ["GDPR"]), 0.9) for i in range(1, 4)]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [
        {"index": 2, "score": 0.5}, {"index": 0, "score": 0.5}, {"index": 1, "score": 0.5},
    ]}))
    with httpx.Client(transport=transport) as client:
        ranked = Retriever(settings, client=client).rerank("问题", hits)
    assert [hit.chunk.id for hit in ranked] == [hit.chunk.id for hit in hits]


@pytest.mark.parametrize("rows", [[], [{"index": 0, "score": 0.4}, {"index": 0, "score": 0.3}]])
def test_incomplete_rerank_response_is_rejected(settings, rows):
    hits = [Hit(Chunk(f"gdpr:rec{i}", "Text", ["GDPR"]), 0.9) for i in range(2)]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": rows}))) as client:
        with pytest.raises(ValueError, match="incomplete or duplicate"):
            Retriever(settings, client=client).rerank("问题", hits)


def test_embedding_response_is_reordered_to_input(settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [
        {"index": 1, "embedding": [0.8, 0.2]}, {"index": 0, "embedding": [0.1, 0.9]},
    ]}))
    with httpx.Client(transport=transport) as client:
        result = Embedder(settings, client).embed(["first", "second"])
    assert result[0][1] == 0.9
    assert result[1][0] == 0.8


@pytest.mark.parametrize("vectors", [[[0.0, 0.0]], [[0.1, 0.9], [0.2]]])
def test_invalid_embedding_vectors_are_rejected(settings, vectors):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [
        {"index": i, "embedding": vector} for i, vector in enumerate(vectors)
    ]}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match="invalid vector"):
            Embedder(settings, client).embed(["text"] * len(vectors))


def test_overlong_embedding_failure_does_not_retry_with_truncation(settings):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "input exceeds context"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            Embedder(settings, client).embed(["A long legal paragraph " * 4000])
    assert len(calls) == 1


def test_health_reports_unavailable_dependencies_without_question_requests(settings):
    def respond(request):
        if request.url.port == 8101:
            return httpx.Response(503)
        if request.url.path.endswith("/points/count"):
            return httpx.Response(200, json={"result": {"count": 0}})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        status = Retriever(settings, client=client).health()
    assert not status["ok"]
    assert not status["embedding"]["ok"]
    assert not status["index"]["ok"]
    assert status["reranker"]["ok"]


def test_unpinned_and_quantized_models_are_rejected(settings):
    lock = json.loads(settings.model_lock_path.read_text())
    lock["embedding"]["revision"] = "main"
    settings.model_lock_path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="full commit hash"):
        embedding_identity(settings)
    lock["embedding"]["revision"] = "a" * 40
    lock["embedding"]["dtype"] = "float16"
    settings.model_lock_path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="BF16"):
        embedding_identity(settings)


def test_configured_model_must_match_lock(settings):
    with pytest.raises(ValueError, match="differs"):
        embedding_identity(replace(settings, embedding_model="some-other-model"))
