from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Lock
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.slides import render_slide_page
from rag.config import Settings


AUTH = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def slide_pages(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()
    with pymupdf.open() as document:
        first = document.new_page(width=600, height=300)
        first.draw_rect(pymupdf.Rect(60, 90, 270, 240), color=(1, 0, 0), fill=(1, 0, 0))
        first.insert_text((30, 45), "PAGE ONE", fontsize=24)
        second = document.new_page(width=300, height=600)
        second.draw_rect(pymupdf.Rect(165, 150, 270, 510), color=(0, 0, 1), fill=(0, 0, 1))
        second.insert_text((30, 45), "PAGE TWO", fontsize=24)
        document.save(slides / "Lecture 6.pdf")
    (slides / "corrupt.pdf").write_text("A broken document with private internal details")
    (slides / "notes.txt").write_text("not a PDF")
    (slides / "outside.pdf").symlink_to(tmp_path / "private.pdf")
    with pymupdf.open() as document:
        document.new_page()
        document.save(tmp_path / "private.pdf")
    app = create_app(Settings(api_key="test-secret"), frontend_dir=tmp_path / "no-frontend",
                     slides_dir=slides)
    return TestClient(app), slides


def pixel_at(image, x_fraction, y_fraction):
    return image.pixel(int(image.width * x_fraction), int(image.height * y_fraction))


def test_requested_pages_preserve_original_layout_and_graphics(slide_pages):
    client, slides = slide_pages
    first_response = client.get("/slides/Lecture%206.pdf/pages/1", headers=AUTH)
    second_response = client.get("/slides/Lecture%206.pdf/pages/2", headers=AUTH)
    assert first_response.status_code == second_response.status_code == 200
    first = pymupdf.Pixmap(first_response.content)
    second = pymupdf.Pixmap(second_response.content)
    assert first.width / first.height == pytest.approx(2, abs=0.01)
    assert second.width / second.height == pytest.approx(0.5, abs=0.01)
    assert 1800 <= max(first.width, first.height) <= 2000
    assert 1800 <= max(second.width, second.height) <= 2000
    red = pixel_at(first, 0.25, 0.5)
    blue = pixel_at(second, 0.75, 0.5)
    assert red[0] > 240 and red[1] < 15 and red[2] < 15
    assert blue[2] > 240 and blue[0] < 15 and blue[1] < 15
    assert min(pixel_at(first, 0.8, 0.5)) > 240
    assert min(pixel_at(second, 0.2, 0.5)) > 240
    # Black title text remains visible in the original top-of-page location.
    dark_samples = sum(
        max(first.pixel(x, y)) < 80
        for y in range(int(first.height * 0.06), int(first.height * 0.18), 3)
        for x in range(int(first.width * 0.04), int(first.width * 0.3), 3)
    )
    assert dark_samples > 30
    assert not list(slides.rglob("*.png"))
    assert first_response.headers["content-type"] == "image/png"
    assert first_response.headers["cache-control"] == "no-store"
    assert first_response.headers["x-content-type-options"] == "nosniff"


def test_large_and_rotated_pages_remain_bounded(slide_pages):
    client, slides = slide_pages
    with pymupdf.open() as document:
        page = document.new_page(width=6000, height=3000)
        page.draw_rect(pymupdf.Rect(0, 0, 6000, 3000), color=(0, 1, 0), fill=(0, 1, 0))
        page.set_rotation(90)
        document.save(slides / "Large.pdf")
    response = client.get("/slides/Large.pdf/pages/1", headers=AUTH)
    assert response.status_code == 200
    image = pymupdf.Pixmap(response.content)
    assert max(image.width, image.height) <= 2000
    assert image.width / image.height == pytest.approx(0.5, abs=0.01)
    assert pixel_at(image, 0.5, 0.5)[1] > 240


@pytest.mark.parametrize("page", ["1", "0", "invalid"])
def test_authentication_precedes_rendering_or_page_validation(slide_pages, page):
    client, _ = slide_pages
    url = f"/slides/Lecture%206.pdf/pages/{page}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("page", ["0", "-1", "invalid", "1.5"])
def test_invalid_page_numbers_are_rejected(slide_pages, page):
    client, _ = slide_pages
    assert client.get(f"/slides/Lecture%206.pdf/pages/{page}", headers=AUTH).status_code == 422


def test_page_beyond_document_is_not_found(slide_pages):
    client, _ = slide_pages
    response = client.get("/slides/Lecture%206.pdf/pages/3", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["detail"] == "Slide page not found."


@pytest.mark.parametrize("filename", [
    "missing.pdf", "notes.txt", "outside.pdf", "..%2Fprivate.pdf", "..%5Cprivate.pdf",
    "%2Fprivate.pdf", ".hidden.pdf", "bad%00.pdf",
])
def test_page_renderer_reuses_pdf_path_safety(slide_pages, filename):
    client, _ = slide_pages
    response = client.get(f"/slides/{filename}/pages/1", headers=AUTH, follow_redirects=False)
    assert response.status_code == 404


def test_corrupt_pdf_returns_generic_error(slide_pages):
    client, slides = slide_pages
    response = client.get("/slides/corrupt.pdf/pages/1", headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {"detail": "Slide PDF could not be rendered."}
    assert "private internal details" not in response.text
    assert str(slides) not in response.text


def test_password_protected_pdf_returns_generic_error(slide_pages):
    client, slides = slide_pages
    with pymupdf.open() as document:
        document.new_page()
        document.save(slides / "Locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
                      owner_pw="owner", user_pw="reader")
    response = client.get("/slides/Locked.pdf/pages/1", headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {"detail": "Slide PDF could not be rendered."}


def test_concurrent_requests_serialize_pymupdf_document_access(slide_pages, monkeypatch):
    _, slides = slide_pages
    original_open = pymupdf.open
    counters = {"active": 0, "peak": 0}
    counter_lock = Lock()

    @contextmanager
    def observed_open(*args, **kwargs):
        with counter_lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        try:
            with original_open(*args, **kwargs) as document:
                time.sleep(0.02)
                yield document
        finally:
            with counter_lock:
                counters["active"] -= 1

    monkeypatch.setattr("api.slides.pymupdf.open", observed_open)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda page: render_slide_page(slides, "Lecture 6.pdf", page), [1, 2]))
    assert all(results)
    assert counters["peak"] == 1
