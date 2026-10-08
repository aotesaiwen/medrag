"""Local dense retrieval and Qwen reranking, without document truncation."""

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from rag.config import TASK, Settings
from rag.types import Chunk, Hit, RetrievalResult

DOCUMENT_INPUT_FORMAT = "heading-path-lines-then-text-v1"
PAYLOAD_OWNER = "course-rag-v1"


@dataclass(frozen=True)
class EmbeddingIdentity:
    repo_id: str
    revision: str
    dtype: str
    input_format: str = DOCUMENT_INPUT_FORMAT

    @property
    def signature(self) -> str:
        """A readable model identity, shared by indexed and query vectors."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def embedding_identity(settings: Settings) -> EmbeddingIdentity:
    lock = json.loads(settings.model_lock_path.read_text(encoding="utf-8"))
    model = lock["embedding"]
    if model["repo_id"] != settings.embedding_model:
        raise ValueError("EMBEDDING_MODEL differs from the pinned model lock")
    if not re.fullmatch(r"[0-9a-f]{40}", model["revision"]):
        raise ValueError("The embedding revision must be a full commit hash")
    dtype = model["dtype"].lower()
    if dtype not in {"bf16", "bfloat16"}:
        raise ValueError("The embedding model must use BF16, as specified")
    return EmbeddingIdentity(model["repo_id"], model["revision"], "bfloat16")


def query_input(question: str) -> str:
    return f"Instruct: {TASK}\nQuery:{question}"


def point_id(chunk_id: str) -> str:
    """Qdrant accepts UUIDs; the human-readable chunk ID stays in its payload."""
    return str(uuid5(NAMESPACE_URL, f"course-rag/chunk/{chunk_id}"))


def point_payload(chunk: Chunk, identity: EmbeddingIdentity) -> dict[str, Any]:
    return {
        "owner": PAYLOAD_OWNER,
        "chunk": chunk.to_dict(),
        "embedding_identity": identity.signature,
    }


def identity_filter(identity: EmbeddingIdentity) -> dict[str, Any]:
    return {"must": [
        {"key": "owner", "match": {"value": PAYLOAD_OWNER}},
        {"key": "embedding_identity", "match": {"value": identity.signature}},
    ]}


class Embedder:
    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings = settings
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.post(
            f"{self.settings.embedding_url.rstrip('/')}/embeddings",
            json={"model": self.settings.embedding_model, "input": texts,
                  "encoding_format": "float"},
        )
        response.raise_for_status()
        rows = response.json()["data"]
        if len(rows) != len(texts) or sorted(row["index"] for row in rows) != list(range(len(texts))):
            raise ValueError("Embedding service returned incomplete or duplicate results")
        vectors = [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
        dimension = len(vectors[0])
        for vector in vectors:
            if (not dimension or len(vector) != dimension
                    or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)
                    or not any(vector)):
                raise ValueError("Embedding service returned an invalid vector")
        return vectors


class VectorStore:
    """A deliberately small Qdrant REST client for this project's collection."""

    def __init__(self, settings: Settings, client: httpx.Client):
        self.client = client
        collection = quote(settings.qdrant_collection, safe="")
        self.url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}"

    def collection(self) -> dict[str, Any] | None:
        response = self.client.get(self.url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["result"]

    def create(self, dimension: int) -> None:
        response = self.client.put(self.url, json={"vectors": {"size": dimension, "distance": "Cosine"}})
        response.raise_for_status()

    def scroll(self) -> list[dict[str, Any]]:
        points = []
        offset = None
        while True:
            body: dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = self.client.post(f"{self.url}/points/scroll", json=body)
            response.raise_for_status()
            result = response.json()["result"]
            points.extend(result["points"])
            offset = result.get("next_page_offset")
            if offset is None:
                return points

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]], identity: EmbeddingIdentity) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Every chunk must have one vector")
        points = [
            {"id": point_id(chunk.id), "vector": vector, "payload": point_payload(chunk, identity)}
            for chunk, vector in zip(chunks, vectors)
        ]
        response = self.client.put(f"{self.url}/points", params={"wait": "true"}, json={"points": points})
        response.raise_for_status()

    def update_payload(self, chunk: Chunk, identity: EmbeddingIdentity) -> None:
        response = self.client.put(
            f"{self.url}/points/payload", params={"wait": "true"},
            json={"points": [point_id(chunk.id)], "payload": point_payload(chunk, identity)},
        )
        response.raise_for_status()

    def delete_points(self, ids: list[str]) -> None:
        if not ids:
            return
        response = self.client.post(
            f"{self.url}/points/delete", params={"wait": "true"}, json={"points": ids},
        )
        response.raise_for_status()

    def search(self, vector: list[float], identity: EmbeddingIdentity) -> list[Hit]:
        response = self.client.post(
            f"{self.url}/points/query",
            json={"query": vector, "limit": 50, "with_payload": True,
                  "with_vector": False, "filter": identity_filter(identity)},
        )
        response.raise_for_status()
        points = response.json()["result"]["points"]
        return [Hit(Chunk.from_dict(p["payload"]["chunk"]), float(p["score"])) for p in points]

    def count(self, identity: EmbeddingIdentity | None = None) -> int:
        body: dict[str, Any] = {"exact": True}
        if identity is not None:
            body["filter"] = identity_filter(identity)
        response = self.client.post(f"{self.url}/points/count", json=body, timeout=3.0)
        response.raise_for_status()
        return int(response.json()["result"]["count"])


class Retriever:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.request_timeout, trust_env=False)
        self._owns_client = client is None
        self.embedder = Embedder(settings, self.client)
        self.store = VectorStore(settings, self.client)

    def retrieve(self, question: str) -> RetrievalResult:
        if not question.strip():
            raise ValueError("Question must not be empty")
        identity = embedding_identity(self.settings)
        vector = self.embedder.embed([query_input(question)])[0]
        dense = self.store.search(vector, identity)
        if not dense:
            raise RuntimeError("The index has no matching vectors; run python -m rag.index")
        return RetrievalResult(dense=dense, reranked=self.rerank(question, dense))

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return []
        # The server's Qwen template supplies TASK. Do not add it to text_1 again.
        # Omitting truncation options makes overlong inputs fail instead of losing text.
        response = self.client.post(
            f"{self.settings.reranker_url.rstrip('/')}/score",
            json={"model": self.settings.reranker_model, "text_1": question,
                  "text_2": [hit.chunk.embedding_text for hit in hits]},
        )
        response.raise_for_status()
        rows = response.json()["data"]
        if len(rows) != len(hits) or sorted(row["index"] for row in rows) != list(range(len(hits))):
            raise ValueError("Reranker returned incomplete or duplicate results")
        scores = {row["index"]: float(row["score"]) for row in rows}
        if not all(math.isfinite(score) for score in scores.values()):
            raise ValueError("Reranker returned a non-finite score")
        order = sorted(range(len(hits)), key=lambda index: -scores[index])[:5]
        return [Hit(hits[index].chunk, scores[index]) for index in order]

    def health(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        endpoints = {
            "embedding": self.settings.embedding_url.rstrip("/").removesuffix("/v1") + "/health",
            "reranker": self.settings.reranker_url.rstrip("/") + "/health",
            "qdrant": self.settings.qdrant_url.rstrip("/") + "/healthz",
        }
        for name, url in endpoints.items():
            try:
                response = self.client.get(url, timeout=3.0)
                response.raise_for_status()
                checks[name] = {"ok": True}
            except httpx.HTTPError as exc:
                checks[name] = {"ok": False, "error": type(exc).__name__}
        try:
            identity = embedding_identity(self.settings)
            total = self.store.count()
            matching = self.store.count(identity)
            checks["index"] = {"ok": total > 0 and total == matching,
                               "points": total, "matching_points": matching}
        except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
            checks["index"] = {"ok": False, "error": type(exc).__name__}
        return {"ok": all(check["ok"] for check in checks.values()), **checks}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
