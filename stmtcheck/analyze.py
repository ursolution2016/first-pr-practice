"""Orchestrates all checks for a statement file."""
from __future__ import annotations

from pathlib import Path

from .arithmetic import check_arithmetic
from .imageforensics import check_exif, check_images, ela_heatmap
from .models import Finding, Severity
from .ocr import IMAGE_SUFFIXES, load_images, ocr_available, ocr_to_text
from .parser import parse_statement
from .pdfforensics import check_layout, check_metadata, extract_lines
from .profiles import check_profile, load_profile

SUPPORTED = {".pdf", ".txt"} | IMAGE_SUFFIXES
OCR_NOTE = " [read by OCR: verify against the image before relying on this]"


def _embedded_images(path: Path) -> list:
    """Native page images of a scanned PDF (better for ELA than a re-render)."""
    from pypdf import PdfReader
    out = []
    for page in PdfReader(str(path)).pages:
        try:
            imgs = [i.image for i in page.images]
        except Exception:
            imgs = []
        if imgs:
            out.append(max(imgs, key=lambda im: im.width * im.height))
    return out


def analyze(path, profile=None, ocr: bool = True, dpi: int = 300, ela_dir=None) -> dict:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"unsupported file type {suffix!r}; use PDF, image or .txt")
    if profile is not None:
        profile = load_profile(profile)

    findings: list = []
    lines = None
    by_ocr = False
    text, page_of_line, align_tol = "", None, 1.0
    page_sizes: list = []
    images: list = []

    if suffix == ".pdf":
        findings += check_metadata(path)
        text, page_of_line, lines, page_sizes = extract_lines(path)
        if not text.strip():
            findings.append(Finding(
                Severity.MEDIUM, "no-text-layer",
                "The PDF has no text layer (scanned or flattened image).",
                evidence="A flattened image of a statement is a common way to hide editing, so "
                         "treat it with caution."))
            images = _embedded_images(path) or load_images(path, dpi)
            lines = None
    elif suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        findings += check_exif(path)
        images = load_images(path, dpi)

    if images:
        findings += check_images(images)
        if ela_dir:
            out_dir = Path(ela_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for pno, img in enumerate(images, 1):
                ela_heatmap(img).save(out_dir / f"{path.stem}-ela-page{pno}.png")
            findings.append(Finding(
                Severity.INFO, "ela-heatmap",
                f"ELA heatmaps written to {out_dir} for visual review.",
                evidence="Look for rectangles or lines of text that glow differently from their "
                         "surroundings. ELA is easily fooled; it is an aid, not a test."))
        if ocr and ocr_available():
            ocr_images = load_images(path, dpi) if suffix == ".pdf" else images
            text, page_of_line, lines = ocr_to_text(ocr_images)
            by_ocr = True
            align_tol = 0.002 * ocr_images[0].width
            findings.append(Finding(
                Severity.INFO, "ocr", f"Text was read by OCR from {len(ocr_images)} page image(s).",
                evidence="OCR can misread digits; confirm any arithmetic finding against the image."))
        elif ocr:
            findings.append(Finding(
                Severity.INFO, "ocr", "OCR is not available (install Tesseract and pytesseract).",
                evidence="Only image-level checks were run."))

    statement = parse_statement(text, page_of_line)
    if by_ocr:
        for t in statement.transactions:
            confs = [w["conf"] for w in lines[t.line_no - 1]["words"][-2:] if "conf" in w]
            t.conf = min(confs) if confs else None
    arithmetic = check_arithmetic(statement, ocr=by_ocr)
    if by_ocr:
        for f in arithmetic:
            if f.severity >= Severity.MEDIUM:
                f.message += OCR_NOTE
    findings += arithmetic
    if lines is not None:
        findings += check_layout(statement, lines, align_tol)
    if profile is not None:
        if suffix == ".pdf" and lines is not None and not by_ocr:
            findings += check_profile(profile, path, lines, page_sizes)
        else:
            findings.append(Finding(
                Severity.INFO, "profile",
                "Profile checks need a PDF with a text layer, so they were skipped."))

    findings.sort(key=lambda f: -int(f.severity))
    top = max((f.severity for f in findings), default=Severity.INFO)
    if top >= Severity.HIGH:
        verdict = "STRONG INDICATORS OF ALTERATION"
    elif top >= Severity.MEDIUM:
        verdict = "SUSPICIOUS - needs manual review"
    else:
        verdict = "NO INDICATORS FOUND (this is not proof the document is genuine)"
    return {
        "file": str(path),
        "rows_checked": len(statement.transactions),
        "verdict": verdict,
        "top_severity": top,
        "findings": findings,
    }
