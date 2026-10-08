"""Deterministic serialization and cache identities shared by ingestion stages."""

import hashlib
import json
from pathlib import Path
from typing import Iterable

from rag.types import Chunk


def fingerprint(data: bytes, *settings: str) -> str:
    digest = hashlib.sha256(data)
    for setting in settings:
        digest.update(b"\0")
        digest.update(setting.encode("utf-8"))
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_chunks(path: Path, chunks: Iterable[Chunk]) -> int:
    values = sorted(chunks, key=lambda chunk: chunk.id)
    ids = [chunk.id for chunk in values]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate chunk IDs: check lecture numbers and legal source units")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for chunk in values:
            stream.write(json.dumps(chunk.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)
    return len(values)


def read_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as stream:
        return [Chunk.from_dict(json.loads(line)) for line in stream if line.strip()]
