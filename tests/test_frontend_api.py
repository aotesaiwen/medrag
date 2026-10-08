from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from rag.config import Settings


AUTH = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def frontend(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Course RAG</title>")
    (assets / "app-123.js").write_text("window.courseRag = true;")
    (assets / "app-123.css").write_text("body { color: black; }")
    (dist / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (dist / ".env").write_text("PRIVATE_KEY=never-public")
    (dist / "package.json").write_text('{"private":"never-public"}')
    (assets / "app-123.js.map").write_text('{"sourcesContent":["never-public"]}')
    hidden = assets / ".private"
    hidden.mkdir()
    (hidden / "secret.js").write_text("never-public")
    slides = tmp_path / "slides"
    slides.mkdir()
    (slides / "Lecture 6.pdf").write_bytes(b"%PDF-1.4\n% sample fixture\n%%EOF")
    (slides / "notes.txt").write_text("never-public")
    (slides / ".hidden.pdf").write_text("never-public")
    secret = tmp_path / "outside.pdf"
    secret.write_text("never-public")
    (slides / "linked.pdf").symlink_to(secret)
    (assets / "linked.js").symlink_to(secret)
    fake = SimpleNamespace(retriever=SimpleNamespace(health=lambda: {"ok": True}))
    app = create_app(Settings(api_key="test-secret"), fake, frontend_dir=dist, slides_dir=slides)
    return TestClient(app), dist, slides


@pytest.mark.parametrize("url, content_type", [
    ("/", "text/html"), ("/index.html", "text/html"),
    ("/assets/app-123.js", "text/javascript"),
    ("/assets/app-123.css", "text/css"), ("/favicon.svg", "image/svg+xml"),
])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_exact_generated_frontend_files_are_public(frontend, url, content_type, method):
    client, _, _ = frontend
    response = client.request(method, url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["x-content-type-options"] == "nosniff"
    if method == "HEAD":
        assert not response.content


@pytest.mark.parametrize("url", [
    "/.env", "/package.json", "/assets/app-123.js.map", "/assets/.private/secret.js",
    "/assets/linked.js", "/assets/missing.js", "/assets/", "/chat/history",
    "/assets/%2e%2e/.env", "/%2e%2e/outside.pdf", "/docs", "/openapi.json",
])
def test_unlisted_files_and_spa_paths_never_bypass_auth(frontend, url):
    client, _, _ = frontend
    public = client.get(url, follow_redirects=False)
    authenticated = client.get(url, headers=AUTH, follow_redirects=False)
    assert public.status_code == 401
    assert authenticated.status_code == 404
    assert "never-public" not in public.text + authenticated.text


@pytest.mark.parametrize("method, url", [
    ("POST", "/"), ("OPTIONS", "/assets/app-123.js"), ("POST", "/ask"),
    ("POST", "/retrieve"), ("GET", "/auth"), ("HEAD", "/health"),
])
def test_static_exception_does_not_relax_other_auth(frontend, method, url):
    client, _, _ = frontend
    assert client.request(method, url).status_code == 401


def test_auth_endpoint_checks_key_without_session_or_key_disclosure(frontend):
    client, _, _ = frontend
    assert client.get("/auth").status_code == 401
    assert client.get("/auth", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/auth", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "set-cookie" not in response.headers
    assert "test-secret" not in response.text


def test_public_frontend_still_loads_if_api_key_missing(frontend):
    _, dist, slides = frontend
    client = TestClient(create_app(Settings(api_key=""), frontend_dir=dist, slides_dir=slides))
    assert client.get("/").status_code == 200
    assert client.get("/auth", headers=AUTH).status_code == 503
    assert client.get("/slides/Lecture%206.pdf", headers=AUTH).status_code == 503


def test_pdf_requires_bearer_and_has_safe_download_headers(frontend):
    client, _, _ = frontend
    url = "/slides/" + quote("Lecture 6.pdf")
    assert client.get(url).status_code == 401
    response = client.get(url, headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("filename", [
    "missing.pdf", "notes.txt", ".hidden.pdf", "linked.pdf", "..%2Foutside.pdf",
    "..%5Coutside.pdf", "%2Foutside.pdf", "nested%2FLecture%206.pdf", "bad%00.pdf",
])
def test_missing_non_pdf_and_escaping_pdf_paths_are_rejected(frontend, filename):
    client, _, _ = frontend
    response = client.get("/slides/" + filename, headers=AUTH, follow_redirects=False)
    assert response.status_code == 404
    assert "never-public" not in response.text


def test_frontend_inventory_does_not_auto_publish_new_files(frontend):
    client, dist, _ = frontend
    (dist / "assets" / "later.js").write_text("new file")
    assert client.get("/assets/later.js").status_code == 401
    assert client.get("/assets/later.js", headers=AUTH).status_code == 404


def test_asset_replaced_by_escaping_symlink_is_no_longer_public(frontend, tmp_path):
    client, dist, _ = frontend
    source = dist / "assets" / "app-123.js"
    source.unlink()
    source.symlink_to(tmp_path / "outside.pdf")
    assert client.get("/assets/app-123.js").status_code == 401
    assert client.get("/assets/app-123.js", headers=AUTH).status_code == 404


def test_missing_frontend_has_no_public_root_or_fallback(tmp_path):
    client = TestClient(create_app(Settings(api_key="test-secret"), frontend_dir=tmp_path / "missing"))
    assert client.get("/").status_code == 401
    assert client.get("/", headers=AUTH).status_code == 404
