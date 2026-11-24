"""Orchestrates all checks for a statement file."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .arithmetic import check_arithmetic
from .imageforensics import check_exif, check_images, ela_heatmap
from .models import Finding, Severity
from .ocr import IMAGE_SUFFIXES, load_images, ocr_available, ocr_to_text
from .parser import parse_statement
from .pdfforensics import check_layout, check_metadata, extract_lines, read_pdf_meta
from .profiles import check_profile, load_profile

SUPPORTED = {".pdf", ".txt"} | IMAGE_SUFFIXES
OCR_NOTE = " [read by OCR: verify against the image before relying on this]"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _embedded_images(path: Path) -> list:
    """Native page images of a scanned PDF (better for forensics than a re-render)."""
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


def read_document(path, ocr: bool = True, dpi: int = 300, ela_dir=None) -> dict:
    """Extract text, lines and document-level findings from a PDF, image or .txt file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"unsupported file type {suffix!r}; use PDF, image or .txt")

    findings: list = []
    lines = None
    by_ocr = False
    text, page_of_line, align_tol = "", None, 1.0
    page_sizes: list = []
    images: list = []
    meta: dict = {}

    if suffix == ".pdf":
        findings += check_metadata(path)
        meta = read_pdf_meta(path)
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
    return {"path": path, "suffix": suffix, "findings": findings, "statement": statement,
            "lines": lines, "by_ocr": by_ocr, "page_sizes": page_sizes, "align_tol": align_tol,
            "meta": meta}


def _verdict(top: Severity) -> str:
    if top >= Severity.HIGH:
        return "STRONG INDICATORS OF ALTERATION"
    if top >= Severity.MEDIUM:
        return "SUSPICIOUS - needs manual review"
    return "NO INDICATORS FOUND (this is not proof the document is genuine)"


def analyze(path, profile=None, ocr: bool = True, dpi: int = 300, ela_dir=None,
            library=None, against=None) -> dict:
    """Run every check. `library` (a Library) + `against` ("auto" or a reference id) adds a
    comparison with a stored known-genuine original."""
    path = Path(path)
    if profile is not None:
        profile = load_profile(profile)
    doc = read_document(path, ocr=ocr, dpi=dpi, ela_dir=ela_dir)
    statement, lines, by_ocr = doc["statement"], doc["lines"], doc["by_ocr"]
    findings = list(doc["findings"])

    arithmetic = check_arithmetic(statement, ocr=by_ocr)
    if by_ocr:
        for f in arithmetic:
            if f.severity >= Severity.MEDIUM:
                f.message += OCR_NOTE
    findings += arithmetic
    if lines is not None:
        findings += check_layout(statement, lines, doc["align_tol"])
    if profile is not None:
        if doc["suffix"] == ".pdf" and lines is not None and not by_ocr:
            findings += check_profile(profile, path, lines, doc["page_sizes"])
        else:
            findings.append(Finding(
                Severity.INFO, "profile",
                "Profile checks need a PDF with a text layer, so they were skipped."))

    result = {
        "file": str(path), "sha256": sha256_file(path), "rows_checked": len(statement.transactions),
        "statement": statement, "lines": lines, "by_ocr": by_ocr, "meta": doc["meta"],
        "page_sizes": doc["page_sizes"], "comparison": None, "reference": None,
        "ocr_suggestions": statement.ocr_suggestions,
    }

    if library is not None and against and against != "none":
        from .compare import compare, make_snapshot
        ref = library.get(against) if against != "auto" else None
        if against != "auto" and ref is None:
            raise ValueError(f"no reference with id {against!r} in the library")
        snap = make_snapshot(result)
        if ref is None:
            ref = library.best_match(snap, result["sha256"])
        if ref is not None:
            comp = compare(snap, ref["snapshot"], result["sha256"], ref["sha256"])
            findings += comp["findings"]
            result["comparison"] = comp
            result["reference"] = ref
        elif against == "auto":
            findings.append(Finding(
                Severity.INFO, "reference",
                "No stored original in the library matches this statement."))

    findings.sort(key=lambda f: -int(f.severity))
    top = max((f.severity for f in findings), default=Severity.INFO)
    result.update(verdict=_verdict(top), top_severity=top, findings=findings)
    return result


def public_view(result: dict) -> dict:
    """JSON-safe subset of an analysis result."""
    comp = result.get("comparison")
    ref = result.get("reference")
    return {
        "file": result["file"], "sha256": result["sha256"], "rows_checked": result["rows_checked"],
        "verdict": result["verdict"], "top_severity": result["top_severity"].name,
        "findings": [f.to_dict() for f in result["findings"]],
        "reference": ({k: ref[k] for k in ("id", "label", "issuer", "added_at")} if ref else None),
        "comparison": ({k: comp[k] for k in ("identical_file", "same_statement", "matched", "overlap",
                                             "diffs", "layout")} if comp else None),
        "ocr_suggestions": result["ocr_suggestions"],
    }
