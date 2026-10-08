"""Render original PDF pages in memory, serializing PyMuPDF access."""

import math
from pathlib import Path
from threading import Lock

import pymupdf
from fastapi import HTTPException

from api.frontend import slide_file


MAX_PAGE_DIMENSION = 2000
PDF_RENDER_LOCK = Lock()


def render_slide_page(directory: Path, filename: str, page_number: int) -> bytes:
    if page_number < 1:
        raise HTTPException(status_code=422, detail="Page numbers start at 1.")
    # PyMuPDF does not support concurrent use from multiple threads. Each API
    # worker serializes the entire document/render lifetime, including PNG encoding.
    with PDF_RENDER_LOCK:
        path = slide_file(directory, filename)
        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise HTTPException(status_code=422, detail="Slide PDF could not be rendered.")
                if page_number > document.page_count:
                    raise HTTPException(status_code=404, detail="Slide page not found.")
                page = document.load_page(page_number - 1)
                width, height = page.rect.width, page.rect.height
                if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                    raise ValueError("Invalid page dimensions")
                # Leave one pixel for rasterizer rounding; preserve the PDF's
                # displayed aspect ratio, rotation, graphics, text, and annotations.
                scale = (MAX_PAGE_DIMENSION - 1) / max(width, height)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                        colorspace=pymupdf.csRGB, alpha=False, annots=True)
                return pixmap.tobytes("png")
        except HTTPException:
            raise
        except Exception:
            # Corrupt-document errors may contain source paths or PDF internals.
            raise HTTPException(status_code=422, detail="Slide PDF could not be rendered.") from None
