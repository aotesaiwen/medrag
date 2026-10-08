"""Small shared value objects for ingestion, retrieval, and answers."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    heading_path: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def embedding_text(self) -> str:
        return "\n".join([*self.heading_path, self.text])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Chunk":
        return cls(**value)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {**self.chunk.to_dict(), "score": self.score}


@dataclass(frozen=True)
class RetrievalResult:
    dense: list[Hit]
    reranked: list[Hit]

    def to_dict(self) -> dict[str, Any]:
        return {"dense": [h.to_dict() for h in self.dense],
                "reranked": [h.to_dict() for h in self.reranked]}
