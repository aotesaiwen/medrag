"""Configuration is read only from the environment and the project's .env."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TASK = "Given a question, retrieve the regulation text or lecture slide content that answers it"


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    api_key: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "course_rag"
    embedding_url: str = "http://127.0.0.1:8101/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    reranker_url: str = "http://127.0.0.1:8102"
    reranker_model: str = "Qwen/Qwen3-Reranker-8B"
    vlm_url: str = "http://127.0.0.1:8103/v1"
    vlm_model: str = "Qwen/Qwen3.8-27B"
    speech_url: str = "http://127.0.0.1:8104"
    speech_timeout: float = 600.0
    request_timeout: float = 180.0
    embedding_batch_size: int = 8
    source_date: str = "2026-10-01"
    build_date: str = "2026-10-05"
    chunks_path: Path = ROOT / "data/processed/chunks.jsonl"
    model_lock_path: Path = ROOT / "config/models.lock.json"

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        values = {**dotenv_values(env_file or ROOT / ".env"), **os.environ}
        fields = cls.__dataclass_fields__
        kwargs = {}
        for name in fields:
            value = values.get(name.upper())
            if value is None:
                continue
            default = fields[name].default
            if isinstance(default, Path):
                path = Path(value).expanduser()
                kwargs[name] = path if path.is_absolute() else ROOT / path
            elif isinstance(default, int):
                kwargs[name] = int(value)
            elif isinstance(default, float):
                kwargs[name] = float(value)
            else:
                kwargs[name] = value
        return cls(**kwargs)
