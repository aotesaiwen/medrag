"""Incrementally index the current corpus without deleting anything by default."""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from rag.config import Settings
from rag.retrieval import (PAYLOAD_OWNER, Embedder, EmbeddingIdentity, VectorStore,
                           embedding_identity, point_id)
from rag.types import Chunk


@dataclass
class IndexPlan:
    embed: list[Chunk] = field(default_factory=list)
    update_payload: list[Chunk] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"embedded": len(self.embed), "metadata_updated": len(self.update_payload),
                "unchanged": len(self.unchanged), "removed": len(self.remove)}


def load_chunks(path: Path) -> list[Chunk]:
    chunks = []
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                chunk = Chunk.from_dict(json.loads(line))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid chunk on line {line_number}") from exc
            if not chunk.id or chunk.id in seen:
                raise ValueError(f"Empty or duplicate chunk ID on line {line_number}: {chunk.id!r}")
            if not isinstance(chunk.text, str) or not isinstance(chunk.heading_path, list):
                raise ValueError(f"Invalid chunk text or heading on line {line_number}")
            if not all(isinstance(part, str) for part in chunk.heading_path):
                raise ValueError(f"Invalid heading path on line {line_number}")
            seen.add(chunk.id)
            chunks.append(chunk)
    if not chunks:
        raise ValueError("The corpus is empty; refusing to replace an index with an empty corpus")
    return chunks


def plan_index(chunks: list[Chunk], points: list[dict[str, Any]], identity: EmbeddingIdentity) -> IndexPlan:
    """Compare the actual embedding inputs; metadata edits do not require inference."""
    plan = IndexPlan()
    existing = {}
    for point in points:
        payload = point.get("payload", {})
        if payload.get("owner") != PAYLOAD_OWNER or "chunk" not in payload:
            raise ValueError("The collection contains data from another application; use a new QDRANT_COLLECTION")
        old_chunk = Chunk.from_dict(payload["chunk"])
        if point["id"] != point_id(old_chunk.id) or old_chunk.id in existing:
            raise ValueError("The collection contains inconsistent or duplicate chunk IDs")
        existing[old_chunk.id] = (old_chunk, payload.get("embedding_identity"))

    current_ids = set()
    for chunk in chunks:
        if chunk.id in current_ids:
            raise ValueError(f"Duplicate chunk ID: {chunk.id}")
        current_ids.add(chunk.id)
        previous = existing.get(chunk.id)
        if previous is None:
            plan.embed.append(chunk)
            continue
        old_chunk, old_identity = previous
        if old_identity != identity.signature or old_chunk.embedding_text != chunk.embedding_text:
            plan.embed.append(chunk)
        elif old_chunk != chunk:
            plan.update_payload.append(chunk)
        else:
            plan.unchanged.append(chunk.id)
    plan.remove = sorted(set(existing) - current_ids)
    return plan


def index_corpus(settings: Settings, *, allow_prune: bool = False, dry_run: bool = False,
                 client: httpx.Client | None = None) -> dict[str, Any]:
    identity = embedding_identity(settings)
    chunks = load_chunks(settings.chunks_path)
    if settings.embedding_batch_size < 1:
        raise ValueError("EMBEDDING_BATCH_SIZE must be positive")
    own_client = client is None
    http = client or httpx.Client(timeout=settings.request_timeout, trust_env=False)
    try:
        store = VectorStore(settings, http)
        embedder = Embedder(settings, http)
        collection = store.collection()
        points = store.scroll() if collection is not None else []
        plan = plan_index(chunks, points, identity)
        report = {**plan.summary(), "total": len(chunks), "dry_run": dry_run,
                  "embedding": json.loads(identity.signature), "stale_chunk_ids": plan.remove}
        if dry_run:
            return report
        if plan.remove and not allow_prune:
            raise ValueError(
                f"{len(plan.remove)} stale indexed chunks need removal. Ask the owner before "
                "rerunning with --allow-prune; use --dry-run to list them. No changes were made."
            )
        dimension = None
        if collection is not None:
            config = collection["config"]["params"]["vectors"]
            if "size" not in config or config.get("distance") != "Cosine":
                raise ValueError("The index requires one unnamed cosine vector; use a new collection")
            dimension = config["size"]
        size = settings.embedding_batch_size
        for start in range(0, len(plan.embed), size):
            batch = plan.embed[start:start + size]
            vectors = embedder.embed([chunk.embedding_text for chunk in batch])
            current_dimension = len(vectors[0])
            if dimension is None:
                store.create(current_dimension)
                dimension = current_dimension
            if current_dimension != dimension:
                raise ValueError("Embedding dimensions changed; use a new collection. Existing data was kept.")
            store.upsert(batch, vectors, identity)
        for chunk in plan.update_payload:
            store.update_payload(chunk, identity)
        # Pruning is last, so a failed embedding batch cannot discard stale vectors.
        if allow_prune:
            store.delete_points([point_id(chunk_id) for chunk_id in plan.remove])
        return report
    finally:
        if own_client:
            http.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inspect the update without changing the index")
    parser.add_argument("--allow-prune", action="store_true", help="Remove stale vectors after owner approval")
    args = parser.parse_args()
    try:
        report = index_corpus(Settings.load(), allow_prune=args.allow_prune, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f"Indexing failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
