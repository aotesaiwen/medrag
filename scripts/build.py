"""Run the cache-aware build, respecting the parser/VLM/serving GPU phases."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag.config import Settings
from scripts.services import owned_process, start, stop, wait_ready


def run(*args: str, env: dict | None = None) -> None:
    subprocess.run([str(ROOT / ".venv/bin/python"), *args], cwd=ROOT, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-prune", action="store_true",
                        help="Owner-authorized removal of stale derived Qdrant points")
    args = parser.parse_args()
    settings = Settings.load()
    lock = json.loads(settings.model_lock_path.read_text())
    if not list((ROOT / "data/raw/slides").glob("*.pdf")):
        parser.error("No slide PDFs found in data/raw/slides; supply them before building")
    speech_was_running = bool(owned_process("speech"))
    for name in ("api", "speech", "embedding", "reranker", "vlm"):
        stop(name)
    start("qdrant", settings)
    wait_ready(settings.qdrant_url + "/healthz", "qdrant")
    run("-m", "ingest", "legal", "--date", settings.source_date)
    run("scripts/parser_stage.py")
    start("vlm", settings)
    try:
        wait_ready(settings.vlm_url.removesuffix("/v1") + "/health", "vlm")
        run("-m", "ingest", "build-slides", "--vlm-base-url", settings.vlm_url,
            "--vlm-model", settings.vlm_model, "--vlm-revision", lock["vlm"]["revision"])
    finally:
        stop("vlm")
    run("-m", "ingest", "assemble", "--tokenizer", str(ROOT / lock["embedding"]["local_path"] / "tokenizer.json"),
        "--output", str(settings.chunks_path))
    start("embedding", settings)
    wait_ready(settings.embedding_url.removesuffix("/v1") + "/health", "embedding")
    start("reranker", settings)
    wait_ready(settings.reranker_url + "/health", "reranker")
    run("-m", "rag.index", *(["--allow-prune"] if args.allow_prune else []))
    rows = [json.loads(line) for line in settings.chunks_path.read_text().splitlines() if line.strip()]
    report = {"build_date": settings.build_date, "source_date": settings.source_date,
              "chunks": len(rows), "by_corpus": dict(Counter(row["metadata"]["corpus"] for row in rows)),
              "models": lock, "python_version": sys.version.split()[0]}
    (ROOT / "data/processed/build_report.json").write_text(json.dumps(report, indent=2) + "\n")
    start("api", settings)
    wait_ready(f"http://{settings.api_host}:{settings.api_port}/health", "api")
    if speech_was_running:
        start("speech", settings)
        wait_ready(settings.speech_url.rstrip('/') + '/health', "speech")
    print("Build complete. Serving models, Qdrant, and API are running.")


if __name__ == "__main__":
    main()
