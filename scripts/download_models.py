"""Download only the official, commit-pinned model artifacts in models.lock.json."""

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*", default=["mineru", "pipeline", "embedding", "reranker", "vlm"])
    args = parser.parse_args()
    lock = json.loads((ROOT / "config/models.lock.json").read_text())
    for name in args.models:
        model = lock[name]
        print(f"Downloading {name}: {model['repo_id']} @ {model['revision']}", flush=True)
        snapshot_download(
            model["repo_id"], revision=model["revision"],
            local_dir=ROOT / model["local_path"],
            allow_patterns=model.get("allow_patterns", ["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "*.tiktoken"]),
            max_workers=4,
        )
        print(f"Ready: {name}", flush=True)


if __name__ == "__main__":
    main()
