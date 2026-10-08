"""Two explicit GPU stages: MinerU parsing, then local image description."""

import base64
import json
import mimetypes
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import pymupdf
from bs4 import BeautifulSoup
from openai import OpenAI

from ingest.common import fingerprint, write_chunks, write_json
from rag.types import Chunk

PARSER_VERSION = "mineru-3.4.5-hybrid-medium-v2"
DESCRIPTION_PROMPT = (
    "Describe this lecture-slide image in clear English for a retrieval system. "
    "State the concepts, visible labels, relationships, chart trends, and meaningful "
    "visual details. Preserve relevant numbers. Do not invent details or refer to "
    "unseen slide context. If decorative, say what it depicts briefly. "
    "Return only the description, without an introductory phrase."
)


def lecture_number(path: Path) -> int:
    # Prefer the explicit lecture label over years or course numbers elsewhere
    # in names such as BME2133_Fall2025_Lecture12.pdf.
    match = re.search(r"lecture[ _-]*0*(\d+)", path.stem, re.I)
    if not match:
        match = re.search(r"(?:^|[^a-z0-9])(?:lec|l)[ _-]*0*(\d+)", path.stem, re.I)
    if not match:
        match = re.search(r"\d+", path.stem)
    if not match:
        raise ValueError(f"Cannot determine lecture number from filename: {path.name}")
    return int(match.group(1) if match.lastindex else match.group())


def slide_files(slides_dir: Path) -> list[Path]:
    paths = sorted((p for p in slides_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                   key=lambda path: (lecture_number(path), path.name))
    if not paths:
        raise ValueError(f"No slide PDFs found in {slides_dir}")
    numbers = [lecture_number(path) for path in paths]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Each slide PDF must have a unique lecture number")
    return paths


def parse_slides(slides_dir: Path, cache_dir: Path, mineru_model_dir: Path, revision: str,
                 pipeline_model_dir: Path, pipeline_revision: str, *,
                 mineru_executable: str = "mineru", mineru_api_url: str | None = None,
                 run: Callable = subprocess.run) -> dict:
    """Only parse files whose bytes or parser model/configuration have changed."""
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not re.fullmatch(r"[a-f0-9]{40}", pipeline_revision):
        raise ValueError("MinerU and its pipeline models require pinned 40-character commit hashes")
    if mineru_api_url:
        from urllib.parse import urlsplit
        parsed_url = urlsplit(mineru_api_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Slide PDFs must be sent only to a local MinerU API")
    cache_dir.mkdir(parents=True, exist_ok=True)
    config = cache_dir / "mineru.json"
    write_json(config, {"config_version": "1.3.2", "model-source": "local", "models-dir": {
        "vlm": str(mineru_model_dir.resolve()), "pipeline": str(pipeline_model_dir.resolve())}})
    environment = os.environ.copy()
    environment.update({"MINERU_MODEL_SOURCE": "local", "MINERU_TOOLS_CONFIG_JSON": str(config.resolve()),
                        "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false", "PYTHONHASHSEED": "0",
                        "MINERU_FORMULA_CH_SUPPORT": "false"})
    entries: dict[str, dict] = {}
    parsed = reused = 0
    for pdf in slide_files(slides_dir):
        key = fingerprint(pdf.read_bytes(), PARSER_VERSION, revision, pipeline_revision)
        output = cache_dir / "parsed" / key
        completion = output / "complete.json"
        if completion.exists():
            entry = json.loads(completion.read_text(encoding="utf-8"))
            if not Path(entry["content_list"]).is_file():
                raise ValueError(f"Incomplete cache at {completion}; expected content list is absent")
            reused += 1
        else:
            output.mkdir(parents=True, exist_ok=True)
            print(f"Parsing {pdf.name}", flush=True)
            command = [mineru_executable, "-p", str(pdf.resolve()), "-o", str(output.resolve()),
                       "-b", "hybrid-engine", "--effort", "medium", "-m", "auto",
                       "--image-analysis", "false"]
            if mineru_api_url:
                command.extend(["--api-url", mineru_api_url])
            run(command, check=True, env=environment)
            candidates = sorted(output.rglob("*_content_list.json"))
            if len(candidates) != 1:
                raise ValueError(f"Expected exactly one MinerU content list for {pdf.name}, found {len(candidates)}")
            with pymupdf.open(pdf) as document:
                page_count = len(document)
            entry = {"parse_key": key, "page_count": page_count,
                     "content_list": str(candidates[0].resolve()), "parser_version": PARSER_VERSION,
                     "parser_revision": revision, "pipeline_revision": pipeline_revision}
            # A completion marker is written only after successful parsing.
            write_json(completion, entry)
            parsed += 1
        entries[pdf.name] = {**entry, "lecture": lecture_number(pdf), "source_file": pdf.name}
    # Deleted source files are omitted. Their cached parser output is retained.
    manifest = {"files": entries, "parsed": parsed, "reused": reused}
    write_json(cache_dir / "manifest.json", manifest)
    return manifest


def image_path(content_list: Path, relative: str) -> Path:
    path = (content_list.parent / relative).resolve()
    root = content_list.parent.resolve()
    if not path.is_relative_to(root):
        raise ValueError("MinerU image path escapes its output directory")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


class ImageDescriber:
    def __init__(self, base_url: str, model: str, revision: str, cache_dir: Path):
        from urllib.parse import urlsplit
        if urlsplit(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Slide images must be sent only to the local VLM")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("The VLM requires a pinned 40-character commit hash")
        self.client = OpenAI(base_url=base_url, api_key="local", timeout=240, max_retries=2)
        self.model, self.revision, self.cache_dir = model, revision, cache_dir
        self.generated = self.reused = 0
        self._counter_lock = threading.Lock()

    def cache_identity(self, path: Path) -> str:
        return fingerprint(path.read_bytes(), self.model, self.revision, "bfloat16", DESCRIPTION_PROMPT)

    def __call__(self, path: Path) -> str:
        data = path.read_bytes()
        key = fingerprint(data, self.model, self.revision, "bfloat16", DESCRIPTION_PROMPT)
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            with self._counter_lock:
                self.reused += 1
            return json.loads(cached.read_text(encoding="utf-8"))["description"]
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(data).decode("ascii")
        for max_tokens in (1536, 4096):
            response = self.client.chat.completions.create(
                model=self.model, temperature=0, seed=0, max_tokens=max_tokens,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": DESCRIPTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ]}], extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            description = (response.choices[0].message.content or "").strip()
            if not description:
                raise ValueError(f"VLM returned an empty description for {path.name}")
            if response.choices[0].finish_reason != "length":
                break
        else:
            raise ValueError(f"VLM returned a truncated description for {path.name} after retry")
        write_json(cached, {"description": description, "model": self.model, "revision": self.revision})
        with self._counter_lock:
            self.generated += 1
        return description


def text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def referenced_images(items: list[dict]) -> list[str]:
    """Return visual inputs in block order, including images inside HTML tables."""
    paths: list[str] = []
    for item in items:
        kind = item.get("type", "text")
        if kind in {"image", "chart"}:
            if not item.get("img_path"):
                raise ValueError("MinerU image block has no extracted image path")
            paths.append(item["img_path"])
        elif kind == "table":
            for body in text_values(item.get("table_body")):
                if "<img" in body.lower():
                    table = BeautifulSoup(body, "html.parser")
                    paths.extend(image["src"] for image in table.find_all("img"))
            if not item.get("table_body") and item.get("img_path"):
                paths.append(item["img_path"])
    return paths


def prefetch_descriptions(paths: list[Path], describe: Callable[[Path], str], workers: int,
                          identity: Callable[[Path], str] | None = None) -> dict[Path, str]:
    """Describe each cache identity once; completion order never affects output."""
    if workers < 1:
        raise ValueError("Image workers must be at least 1")
    identify = identity or (lambda path: str(path.resolve()))
    groups: dict[str, list[Path]] = {}
    for path in dict.fromkeys(paths):
        groups.setdefault(identify(path), []).append(path)
    results: dict[Path, str] = {}
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(describe, members[0]): members for members in groups.values()}
        for future in as_completed(futures):
            try:
                description = future.result()
                for path in futures[future]:
                    results[path] = description
            except Exception as error:
                errors.append(error)
    if errors:
        raise RuntimeError(f"{len(errors)} image description request(s) failed") from errors[0]
    return results


def chunks_from_content(items: list[dict], page_count: int, lecture: int, source_file: str,
                        describe: Callable[[str], str], metadata: dict | None = None) -> list[Chunk]:
    pages: list[list[str]] = [[] for _ in range(page_count)]
    for item in items:
        page = int(item.get("page_idx", 0))
        if not 0 <= page < page_count:
            raise ValueError(f"MinerU page index {page} outside PDF page count {page_count}")
        kind = item.get("type", "text")
        parts: list[str] = []
        if kind in {"image", "chart"}:
            if not item.get("img_path"):
                raise ValueError("MinerU image block has no extracted image path")
            parts.append("[Image: " + describe(item["img_path"]) + "]")
            parts.extend(text_values(item.get(f"{kind}_caption")))
            parts.extend(text_values(item.get(f"{kind}_footnote")))
            if kind == "chart":
                parts.extend(text_values(item.get("content")))
        elif kind == "table":
            parts.extend(text_values(item.get("table_caption")))
            for body in text_values(item.get("table_body")):
                if "<img" in body.lower():
                    table = BeautifulSoup(body, "html.parser")
                    for image in table.find_all("img"):
                        image.replace_with("[Image: " + describe(image["src"]) + "]")
                    body = str(table)
                parts.append(body)
            # A table with no extracted text still has meaningful visual content.
            if not item.get("table_body") and item.get("img_path"):
                parts.append("[Image: " + describe(item["img_path"]) + "]")
            parts.extend(text_values(item.get("table_footnote")))
        elif kind == "list":
            parts.extend(text_values(item.get("list_items")))
        else:
            parts.extend(text_values(item.get("text")))
            if kind == "code":
                parts.extend(text_values(item.get("code_caption")))
                parts.extend(text_values(item.get("code_body")))
        pages[page].extend(parts)
    return [Chunk(f"slides:lecture{lecture}:s{page + 1}", "\n\n".join(parts),
                  [f"Lecture {lecture}", f"Slide {page + 1}"],
                  {**(metadata or {}), "corpus": "slides", "lecture": lecture, "slide": page + 1,
                   "source_file": source_file, "citation_label": f"Lecture {lecture}, Slide {page + 1}"})
            for page, parts in enumerate(pages)]


def build_slides(slides_dir: Path, cache_dir: Path, output: Path, describe: Callable[[Path], str],
                 source_date: str, vlm_revision: str, *, image_workers: int = 16) -> int:
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    documents: list[tuple[Path, dict, Path, list[dict]]] = []
    images: list[Path] = []
    for pdf in slide_files(slides_dir):
        entry = manifest["files"].get(pdf.name)
        if not entry:
            raise ValueError(f"Run parse-slides for new PDF {pdf.name} first")
        expected_key = fingerprint(pdf.read_bytes(), entry["parser_version"], entry["parser_revision"], entry["pipeline_revision"])
        if expected_key != entry["parse_key"]:
            raise ValueError(f"Run parse-slides for changed PDF {pdf.name} first")
        content_list = Path(entry["content_list"])
        items = json.loads(content_list.read_text(encoding="utf-8"))
        documents.append((pdf, entry, content_list, items))
        images.extend(image_path(content_list, relative) for relative in referenced_images(items))
    identity = describe.cache_identity if isinstance(describe, ImageDescriber) else None
    print(f"Describing {len(images)} slide image references with {image_workers} workers", flush=True)
    descriptions = prefetch_descriptions(images, describe, image_workers, identity)
    chunks: list[Chunk] = []
    for pdf, entry, content_list, items in documents:
        chunks.extend(chunks_from_content(items, entry["page_count"], entry["lecture"], pdf.name,
                      lambda relative: descriptions[image_path(content_list, relative)],
                      {"source_date": source_date, "parser_revision": entry["parser_revision"],
                       "pipeline_revision": entry["pipeline_revision"], "vlm_revision": vlm_revision,
                       "parser_version": entry["parser_version"]}))
        print(f"Built {pdf.name}: {entry['page_count']} slides", flush=True)
    return write_chunks(output, chunks)
