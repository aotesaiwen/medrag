"""Run separately scheduled ingestion stages with explicit, reproducible settings."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ingest.common import read_chunks, write_chunks, write_json
from ingest.legal import build_legal
from ingest.slides import ImageDescriber, build_slides, parse_slides


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source_date = os.getenv("SOURCE_DATE", "2026-10-01")
    legal = commands.add_parser("legal")
    legal.add_argument("--date", default=source_date)
    legal.add_argument("--raw-dir", type=Path, default=Path("data/raw/legal"))
    legal.add_argument("--output", type=Path, default=Path("data/processed/legal.jsonl"))
    parse = commands.add_parser("parse-slides")
    parse.add_argument("--slides-dir", type=Path, default=Path("data/raw/slides"))
    parse.add_argument("--cache-dir", type=Path, default=Path("data/cache/slides"))
    parse.add_argument("--mineru-model-dir", type=Path, required=True)
    parse.add_argument("--revision", required=True)
    parse.add_argument("--pipeline-model-dir", type=Path, required=True)
    parse.add_argument("--pipeline-revision", required=True)
    parse.add_argument("--mineru-executable", default="mineru")
    parse.add_argument("--mineru-api-url", help="Reuse an already running local MinerU API")
    slides = commands.add_parser("build-slides")
    slides.add_argument("--slides-dir", type=Path, default=Path("data/raw/slides"))
    slides.add_argument("--cache-dir", type=Path, default=Path("data/cache/slides"))
    slides.add_argument("--output", type=Path, default=Path("data/processed/slides.jsonl"))
    slides.add_argument("--date", default=source_date)
    slides.add_argument("--vlm-base-url", default=os.getenv("VLM_URL", "http://127.0.0.1:8103/v1"))
    slides.add_argument("--vlm-model", default=os.getenv("VLM_MODEL", "Qwen/Qwen3.8-27B"))
    slides.add_argument("--vlm-revision", required=True)
    slides.add_argument("--image-workers", type=int, default=16)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--inputs", nargs="+", type=Path, default=[Path("data/processed/legal.jsonl"), Path("data/processed/slides.jsonl")])
    assemble.add_argument("--output", type=Path, default=Path("data/processed/chunks.jsonl"))
    assemble.add_argument("--report", type=Path, default=Path("data/processed/oversized_chunks.json"))
    assemble.add_argument("--tokenizer", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "legal":
        count = build_legal(args.raw_dir, args.output, args.date)
        print(json.dumps({"chunks": count, "output": str(args.output)}))
    elif args.command == "parse-slides":
        result = parse_slides(args.slides_dir, args.cache_dir, args.mineru_model_dir, args.revision,
                              args.pipeline_model_dir, args.pipeline_revision,
                              mineru_executable=args.mineru_executable, mineru_api_url=args.mineru_api_url)
        print(json.dumps({"files": len(result["files"]), "parsed": result["parsed"], "reused": result["reused"]}))
    elif args.command == "build-slides":
        describe = ImageDescriber(args.vlm_base_url, args.vlm_model, args.vlm_revision, args.cache_dir / "descriptions")
        count = build_slides(args.slides_dir, args.cache_dir, args.output, describe, args.date,
                             args.vlm_revision, image_workers=args.image_workers)
        print(json.dumps({"chunks": count, "images_generated": describe.generated, "images_reused": describe.reused}))
    else:
        from tokenizers import Tokenizer
        tokenizer_path = args.tokenizer / "tokenizer.json" if args.tokenizer.is_dir() else args.tokenizer
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        chunks = [chunk for path in args.inputs for chunk in read_chunks(path)]
        count = write_chunks(args.output, chunks)
        oversized = []
        for chunk in sorted(chunks, key=lambda item: item.id):
            tokens = len(tokenizer.encode(chunk.embedding_text, add_special_tokens=False).ids)
            if tokens > 1000:
                oversized.append({"id": chunk.id, "tokens": tokens, "heading_path": chunk.heading_path})
        write_json(args.report, {"threshold": 1000, "tokenizer": str(tokenizer_path), "count": len(oversized), "chunks": oversized})
        print(json.dumps({"chunks": count, "oversized": len(oversized), "report": str(args.report)}))


if __name__ == "__main__":
    main()
