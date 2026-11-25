"""Bridge to the Android app's Java helpers (OcrBridge, PdfBridge). Only usable under Chaquopy.

On a phone there is no Tesseract binary and no pdfium wheel, so OCR uses Google ML Kit and PDF pages
are rendered with Android's PdfRenderer. Both live in the Java side of the app and return JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from PIL import Image


def available() -> bool:
    try:
        import java  # noqa: F401  (only exists under Chaquopy)
    except ImportError:
        return False
    return True


def _cls(name: str):
    from java import jclass
    return jclass(name)


def render_pdf(path, dpi: int) -> list:
    """Render every page of a PDF to a PIL image using android.graphics.pdf.PdfRenderer."""
    out = tempfile.mkdtemp(prefix="pdfpages-")
    try:
        names = json.loads(str(_cls("app.stmtcheck.PdfBridge").render(str(path), int(dpi), out)))
        pages = []
        for n in names:
            with Image.open(n) as im:
                pages.append(im.convert("RGB"))
        return pages
    finally:
        shutil.rmtree(out, ignore_errors=True)


def recognize(img: Image.Image) -> list:
    """OCR one page image with ML Kit. Returns word dicts: text, x0, x1, top, bottom, conf (0-100)."""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp)
        return json.loads(str(_cls("app.stmtcheck.OcrBridge").recognize(tmp)))
    finally:
        os.unlink(tmp)
