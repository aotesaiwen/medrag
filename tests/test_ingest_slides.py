import json
from pathlib import Path

import pymupdf
import pytest

from ingest.common import read_chunks, write_chunks
from ingest.slides import ImageDescriber, chunks_from_content, image_path, lecture_number, parse_slides, prefetch_descriptions, slide_files
from rag.types import Chunk


def test_slide_boundaries_blank_pages_images_and_latex():
    seen = []
    def describe(path):
        seen.append(path)
        return "An erasure workflow."
    chunks = chunks_from_content([
        {"type": "text", "text": "Privacy", "page_idx": 0},
        {"type": "image", "img_path": "images/a.jpg", "image_caption": ["Figure 1"], "page_idx": 0},
        {"type": "equation", "text": "$$E=mc^2$$", "page_idx": 2},
    ], 3, 6, "Lecture6.pdf", describe)
    assert [c.id for c in chunks] == ["slides:lecture6:s1", "slides:lecture6:s2", "slides:lecture6:s3"]
    assert chunks[1].text == ""
    assert "[Image: An erasure workflow.]" in chunks[0].text
    assert chunks[2].text == "$$E=mc^2$$"
    assert seen == ["images/a.jpg"]
    assert all(c.heading_path[0] == "Lecture 6" for c in chunks)


def test_tables_lists_and_headers_preserved():
    chunks = chunks_from_content([
        {"type": "header", "text": "Course", "page_idx": 0},
        {"type": "list", "list_items": ["First", "Second"], "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>Retention</td></tr></table>", "page_idx": 0},
    ], 1, 2, "Lecture2.pdf", lambda _: pytest.fail("No image description required"))
    assert all(term in chunks[0].text for term in ["Course", "First", "Second", "Retention"])


def test_charts_and_embedded_table_images_are_described():
    described = []
    def describe(path):
        described.append(path)
        return "Visual content."
    chunks = chunks_from_content([
        {"type": "chart", "img_path": "images/chart.png", "chart_caption": ["Growth"], "page_idx": 0},
        {"type": "table", "table_body": '<table><tr><td><img src="images/icon.png"></td></tr></table>', "page_idx": 0},
    ], 1, 2, "Lecture2.pdf", describe)
    assert described == ["images/chart.png", "images/icon.png"]
    assert chunks[0].text.count("[Image: Visual content.]") == 2
    assert "Growth" in chunks[0].text


def test_out_of_range_page_is_rejected():
    with pytest.raises(ValueError, match="outside"):
        chunks_from_content([{"page_idx": 4, "text": "Wrong"}], 1, 1, "1.pdf", lambda _: "")


def test_image_paths_cannot_escape_parser_output(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        image_path(tmp_path / "content.json", "../other.png")


def make_pdf(path: Path, pages: int = 1):
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page()
        document.save(path)


def test_incremental_parser_reuses_unchanged_files_and_omits_removed_sources(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()
    make_pdf(slides / "Lecture1.pdf")
    make_pdf(slides / "Lecture2.pdf")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        out = Path(command[command.index("-o") + 1])
        (out / "deck_content_list.json").write_text("[]")
    args = [slides, tmp_path / "cache", tmp_path / "models", "a" * 40, tmp_path / "pipeline", "b" * 40]
    first = parse_slides(*args, run=run)
    assert first["parsed"] == 2
    second = parse_slides(*args, run=run)
    assert second["reused"] == 2
    assert len(calls) == 2
    # Replacing a fixture PDF simulates the owner's new revision.
    (slides / "Lecture1.pdf").unlink()
    make_pdf(slides / "Lecture1.pdf", pages=2)
    (slides / "Lecture2.pdf").rename(tmp_path / "removed.pdf")
    third = parse_slides(*args, run=run)
    assert third["parsed"] == 1
    assert list(third["files"]) == ["Lecture1.pdf"]
    assert third["files"]["Lecture1.pdf"]["page_count"] == 2
    assert len(calls) == 3


def test_failed_parser_never_marks_cache_complete(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()
    make_pdf(slides / "Lecture1.pdf")
    def run(*args, **kwargs):
        raise RuntimeError("Parser failed")
    with pytest.raises(RuntimeError, match="Parser failed"):
        parse_slides(slides, tmp_path / "cache", tmp_path / "models", "a" * 40,
                     tmp_path / "pipeline", "b" * 40, run=run)
    assert not list((tmp_path / "cache").rglob("complete.json"))


@pytest.mark.parametrize("api_url", [None, "http://127.0.0.1:8104"])
def test_parser_optionally_reuses_local_api_without_changing_parse_settings(tmp_path, api_url):
    slides = tmp_path / "slides"
    slides.mkdir()
    make_pdf(slides / "Lecture1.pdf")
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        output = Path(command[command.index("-o") + 1])
        (output / "deck_content_list.json").write_text("[]")
    result = parse_slides(slides, tmp_path / "cache", tmp_path / "models", "a" * 40,
                          tmp_path / "pipeline", "b" * 40, mineru_api_url=api_url, run=run)
    assert result["parsed"] == 1
    assert commands[0][commands[0].index("-b") + 1] == "hybrid-engine"
    if api_url:
        assert commands[0][commands[0].index("--api-url") + 1] == api_url
    else:
        assert "--api-url" not in commands[0]


def test_parser_rejects_remote_api(tmp_path):
    with pytest.raises(ValueError, match="local MinerU"):
        parse_slides(tmp_path, tmp_path / "cache", tmp_path / "models", "a" * 40,
                     tmp_path / "pipeline", "b" * 40, mineru_api_url="https://example.com")


def test_duplicate_lecture_numbers_rejected(tmp_path):
    (tmp_path / "Lecture6.pdf").touch()
    (tmp_path / "Lecture06-copy.pdf").touch()
    with pytest.raises(ValueError, match="unique lecture"):
        slide_files(tmp_path)


@pytest.mark.parametrize("name,number", [("Lecture_06.pdf", 6), ("lec-17-v2.pdf", 17),
                                       ("06-privacy.pdf", 6), ("BME2133_Fall2025_Lecture12.pdf", 12),
                                       ("Fall2025-L6.pdf", 6)])
def test_lecture_number_from_filename(name, number):
    assert lecture_number(Path(name)) == number


def test_serialized_chunks_round_trip_and_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "chunks.jsonl"
    chunk = Chunk("gdpr:art16", "Correction", ["GDPR", "Article 16"])
    write_chunks(path, [chunk])
    assert read_chunks(path)[0].text == "Correction"
    with pytest.raises(ValueError, match="Duplicate chunk"):
        write_chunks(path, [chunk, chunk])


def test_prefetch_deduplicates_cache_identity_across_different_paths(tmp_path):
    a, b, c = [tmp_path / name for name in ["a.png", "b.png", "c.png"]]
    calls = []
    def describe(path):
        calls.append(path)
        return f"Description for {path.name}"
    result = prefetch_descriptions([a, b, a, c], describe, 4,
                                  lambda path: "same-image" if path in {a, b} else "different")
    assert len(calls) == 2
    assert result[a] == result[b]
    assert result[c] == "Description for c.png"


def test_prefetch_finishes_all_requests_before_raising(tmp_path):
    import threading
    completed = []
    lock = threading.Lock()
    paths = [tmp_path / f"{i}.png" for i in range(5)]
    def describe(path):
        with lock:
            completed.append(path)
        if path in {paths[1], paths[3]}:
            raise ValueError("Simulated failure")
        return "Complete"
    with pytest.raises(RuntimeError, match="2 image description"):
        prefetch_descriptions(paths, describe, 4)
    assert set(completed) == set(paths)


def test_prefetch_enforces_bounded_concurrency(tmp_path):
    import threading
    active = maximum = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    def describe(path):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return path.name
    result = prefetch_descriptions([tmp_path / f"{i}.png" for i in range(4)], describe, 2)
    assert maximum == 2
    assert len(result) == 4


def make_fake_describer(tmp_path, monkeypatch, replies):
    from types import SimpleNamespace
    requests = []
    responses = iter(replies)
    def create(**kwargs):
        requests.append(kwargs)
        text, reason = next(responses)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason=reason)])
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("ingest.slides.OpenAI", lambda **kwargs: fake_client)
    describer = ImageDescriber("http://127.0.0.1:8103/v1", "Qwen/Qwen3.8-27B", "a" * 40, tmp_path / "cache")
    path = tmp_path / "image.png"
    path.write_bytes(b"offline test image")
    return describer, path, requests


def test_description_retries_truncation_then_caches_complete_response(tmp_path, monkeypatch):
    describer, path, requests = make_fake_describer(tmp_path, monkeypatch,
                                                   [("Partial description", "length"), ("Complete description", "stop")])
    assert describer(path) == "Complete description"
    assert [request["max_tokens"] for request in requests] == [1536, 4096]
    assert all(request["temperature"] == 0 and request["seed"] == 0 for request in requests)
    assert describer(path) == "Complete description"
    assert len(requests) == 2
    assert describer.generated == 1
    assert describer.reused == 1


def test_persistent_description_truncation_is_not_cached(tmp_path, monkeypatch):
    describer, path, requests = make_fake_describer(tmp_path, monkeypatch,
                                                   [("Partial", "length"), ("Still partial", "length")])
    with pytest.raises(ValueError, match="truncated.*after retry"):
        describer(path)
    assert [request["max_tokens"] for request in requests] == [1536, 4096]
    assert not list((tmp_path / "cache").glob("*.json"))
    assert describer.generated == 0


def test_empty_description_is_not_retried_or_cached(tmp_path, monkeypatch):
    describer, path, requests = make_fake_describer(tmp_path, monkeypatch, [("  ", "stop")])
    with pytest.raises(ValueError, match="empty description"):
        describer(path)
    assert len(requests) == 1
    assert not list((tmp_path / "cache").glob("*.json"))
