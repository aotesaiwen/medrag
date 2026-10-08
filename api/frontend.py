"""Explicit public build assets and confined, authenticated slide downloads."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse


ASSET_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


def contained_file(root: Path, candidate: Path) -> bool:
    """Reject missing files and symlinks, including replacements after startup."""
    try:
        resolved = candidate.resolve(strict=True)
        return resolved == candidate and resolved.is_relative_to(root) and resolved.is_file()
    except (OSError, RuntimeError):
        return False


class FrontendFiles:
    """Freeze a small, exact URL inventory when the built frontend is loaded."""

    def __init__(self, directory: Path):
        self.root = directory.resolve()
        self.files: dict[str, tuple[Path, str]] = {}
        if not self.root.is_dir():
            return
        index = self.root / "index.html"
        if contained_file(self.root, index):
            self.files["/"] = (index, "text/html")
            self.files["/index.html"] = (index, "text/html")
        for candidate in sorted(self.root.rglob("*")):
            relative = candidate.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            media_type = ASSET_TYPES.get(candidate.suffix.lower())
            if media_type and contained_file(self.root, candidate):
                self.files["/" + relative.as_posix()] = (candidate, media_type)

    def lookup(self, url: str) -> tuple[Path, str] | None:
        asset = self.files.get(url)
        if asset is not None and contained_file(self.root, asset[0]):
            return asset
        return None

    def register(self, application: FastAPI) -> None:
        def endpoint(url: str):
            def serve_asset() -> FileResponse:
                asset = self.lookup(url)
                if asset is None:
                    raise HTTPException(status_code=404, detail="Asset not found.")
                path, media_type = asset
                return FileResponse(path, media_type=media_type, headers={
                    "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
                })
            return serve_asset

        # Register only actual files; there is no SPA fallback or directory mount.
        for url in self.files:
            application.add_api_route(url, endpoint(url), methods=["GET", "HEAD"],
                                      include_in_schema=False)


def slide_file(directory: Path, filename: str) -> Path:
    root = directory.resolve()
    if (not filename or filename.startswith(".") or "/" in filename or "\\" in filename
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)
            or Path(filename).suffix.lower() != ".pdf"):
        raise HTTPException(status_code=404, detail="Slide PDF not found.")
    candidate = root / filename
    if not contained_file(root, candidate):
        raise HTTPException(status_code=404, detail="Slide PDF not found.")
    return candidate
