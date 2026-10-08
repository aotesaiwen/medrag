import json
from dataclasses import replace

import httpx
import pytest

from rag.config import Settings
from rag.index import index_corpus, load_chunks, plan_index
from rag.retrieval import EmbeddingIdentity, point_id, point_payload
from rag.types import Chunk

IDENTITY = EmbeddingIdentity("Qwen/Qwen3-Embedding-8B", "a" * 40, "bfloat16")


def stored(chunk, identity=IDENTITY):
    return {"id": point_id(chunk.id), "payload": point_payload(chunk, identity)}


def test_plan_embeds_only_new_or_changed_inputs():
    old = Chunk("gdpr:art17:p1", "Old legal text", ["GDPR", "Article 17"], {"url": "old"})
    unchanged = Chunk("gdpr:art6:p1", "Lawfulness", ["GDPR", "Article 6"])
    metadata_only = Chunk("gdpr:rec65", "Erasure", ["GDPR", "Recital 65"], {"url": "old"})
    deleted = Chunk("slides:lecture6:s7", "Old slide", ["Lecture 6"])
    new = Chunk("slides:lecture7:s1", "", ["Lecture 7"])
    current = [replace(old, text="New legal text"), unchanged,
               replace(metadata_only, metadata={"url": "new"}), new]
    plan = plan_index(current, [stored(chunk) for chunk in [old, unchanged, metadata_only, deleted]], IDENTITY)
    assert {chunk.id for chunk in plan.embed} == {old.id, new.id}
    assert [chunk.id for chunk in plan.update_payload] == [metadata_only.id]
    assert plan.unchanged == [unchanged.id]
    assert plan.remove == [deleted.id]


@pytest.mark.parametrize("changed", [
    replace(IDENTITY, repo_id="Qwen/a-new-embedding-model"),
    replace(IDENTITY, revision="b" * 40),
    replace(IDENTITY, dtype="float16"),
    replace(IDENTITY, input_format="heading-path-v2"),
])
def test_embedding_identity_changes_reembed_every_chunk(changed):
    chunks = [Chunk("gdpr:rec1", "Text", ["GDPR"]), Chunk("gdpr:rec2", "More text", ["GDPR"])]
    plan = plan_index(chunks, [stored(chunk) for chunk in chunks], changed)
    assert len(plan.embed) == 2
    assert not plan.unchanged


def test_heading_edit_changes_the_embedding_input():
    old = Chunk("gdpr:art17:p1", "Erasure", ["GDPR", "Article 17"])
    plan = plan_index([replace(old, heading_path=["GDPR", "Article 17: Right to erasure"])], [stored(old)], IDENTITY)
    assert len(plan.embed) == 1


def test_foreign_collection_is_never_reused():
    with pytest.raises(ValueError, match="another application"):
        plan_index([], [{"id": 1, "payload": {"text": "foreign data"}}], IDENTITY)


def test_duplicate_ids_are_rejected():
    chunk = Chunk("gdpr:rec1", "Text", ["GDPR"])
    with pytest.raises(ValueError, match="Duplicate chunk ID"):
        plan_index([chunk, chunk], [], IDENTITY)


def test_loading_retains_blank_slides_and_rejects_empty_corpus(tmp_path):
    path = tmp_path / "chunks.jsonl"
    path.write_text(json.dumps(Chunk("slides:lecture6:s1", "", ["Lecture 6"]).to_dict()) + "\n")
    assert load_chunks(path)[0].text == ""
    path.write_text("\n")
    with pytest.raises(ValueError, match="corpus is empty"):
        load_chunks(path)


def configure(tmp_path, chunks):
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"embedding": {
        "repo_id": IDENTITY.repo_id, "revision": IDENTITY.revision, "dtype": IDENTITY.dtype,
    }}))
    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text("".join(json.dumps(chunk.to_dict()) + "\n" for chunk in chunks))
    return Settings(model_lock_path=lock, chunks_path=corpus, embedding_batch_size=2)


class LocalServices:
    """An in-memory HTTP fixture; tests never call a model or a real database."""

    def __init__(self, chunks=(), *, exists=True):
        self.points = {point_id(chunk.id): stored(chunk) for chunk in chunks}
        self.exists = exists
        self.embedded = []
        self.mutations = []

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET":
            if not self.exists:
                return httpx.Response(404)
            return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {
                "size": 2, "distance": "Cosine",
            }}}}})
        if path.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": list(self.points.values()), "next_page_offset": None}})
        if path == "/v1/embeddings":
            self.embedded.extend(body["input"])
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": [0.25, 0.75]} for i in range(len(body["input"]))
            ]})
        self.mutations.append((request.method, path))
        if path.endswith("/points/delete"):
            for item in body["points"]:
                del self.points[item]
        elif path.endswith("/points/payload"):
            for item in body["points"]:
                self.points[item]["payload"] = body["payload"]
        elif path.endswith("/points"):
            for point in body["points"]:
                self.points[point["id"]] = point
        else:
            self.exists = True
        return httpx.Response(200, json={"result": {"status": "completed"}})


def test_initial_index_and_repeat_do_not_repeat_inference(tmp_path):
    chunks = [Chunk("gdpr:rec1", "Text", ["GDPR"]), Chunk("slides:lecture1:s1", "", ["Lecture 1"])]
    settings = configure(tmp_path, chunks)
    services = LocalServices(exists=False)
    with httpx.Client(transport=httpx.MockTransport(services)) as client:
        first = index_corpus(settings, client=client)
        second = index_corpus(settings, client=client)
    assert first["embedded"] == 2
    assert second["embedded"] == 0
    assert second["unchanged"] == 2
    assert len(services.embedded) == 2
    assert len(services.points) == 2


def test_pruning_requires_approval_before_any_mutation(tmp_path):
    old = Chunk("gdpr:rec1", "Text", ["GDPR"])
    new = Chunk("gdpr:rec2", "New text", ["GDPR"])
    settings = configure(tmp_path, [new])
    services = LocalServices([old])
    with httpx.Client(transport=httpx.MockTransport(services)) as client:
        with pytest.raises(ValueError, match="Ask the owner"):
            index_corpus(settings, client=client)
        preview = index_corpus(settings, dry_run=True, client=client)
    assert preview["stale_chunk_ids"] == [old.id]
    assert not services.mutations
    assert not services.embedded


def test_approved_pruning_runs_after_embedding(tmp_path):
    old = Chunk("gdpr:rec1", "Text", ["GDPR"])
    new = Chunk("gdpr:rec2", "New text", ["GDPR"])
    settings = configure(tmp_path, [new])
    services = LocalServices([old])
    with httpx.Client(transport=httpx.MockTransport(services)) as client:
        report = index_corpus(settings, allow_prune=True, client=client)
    assert report["removed"] == 1
    assert services.mutations[-1][1].endswith("/points/delete")
    assert point_id(new.id) in services.points
    assert point_id(old.id) not in services.points


def test_metadata_only_change_does_not_call_embedding(tmp_path):
    old = Chunk("gdpr:rec1", "Text", ["GDPR"], {"source_url": "old"})
    new = replace(old, metadata={"source_url": "new"})
    settings = configure(tmp_path, [new])
    services = LocalServices([old])
    with httpx.Client(transport=httpx.MockTransport(services)) as client:
        report = index_corpus(settings, client=client)
    assert report["metadata_updated"] == 1
    assert not services.embedded
    assert services.points[point_id(old.id)]["payload"]["chunk"]["metadata"]["source_url"] == "new"
