"""PDF-level checks: edit history, producer metadata, font and alignment consistency.

Line records (shared with the OCR module) are dicts:
    {"page": int, "text": str, "words": [word, ...], "source": "text" | "ocr"}
and words are dicts with x0, x1, top, bottom, text, and optionally fonts (frozenset of
(fontname, size)) for text-layer PDFs or conf (0-100) for OCR.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import pdfplumber
from pypdf import PdfReader

from .models import Finding, ParsedStatement, Severity

# Tools people use to edit an existing PDF, or that re-render page content.
EDITOR_MARKERS = [
    "acrobat pro", "adobe acrobat", "photoshop", "illustrator", "indesign", "foxit", "phantompdf",
    "pdfescape", "sejda", "ilovepdf", "smallpdf", "pdf-xchange", "nitro", "pdfelement",
    "wondershare", "canva", "microsoft word", "libreoffice", "openoffice", "skia/pdf",
    "quartz pdfcontext", "pdf-lib", "pdfsam", "pdftk", "ghostscript", "pdf24", "soda pdf",
    "pdfcandy", "docfly", "dochub", "lightpdf", "online2pdf", "gimp", "paint.net", "pixelmator",
    "affinity", "snapseed", "picsart", "lightroom",
]


def _parse_pdf_date(s):
    if not s:
        return None
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", str(s))
    if not m:
        return None
    parts = [int(x) if x else d for x, d in zip(m.groups(), (0, 1, 1, 0, 0, 0))]
    try:
        return datetime(*parts)
    except ValueError:
        return None


def read_pdf_meta(path: Path) -> dict:
    meta = PdfReader(str(path)).metadata or {}
    return {k: str(meta.get(k, "") or "") for k in ("/Producer", "/Creator", "/CreationDate", "/ModDate")}


def check_metadata(path: Path) -> list:
    findings: list = []
    raw = path.read_bytes()
    meta = read_pdf_meta(path)

    eof_count = len(re.findall(rb"%%EOF", raw))
    linearized = b"/Linearized" in raw[:2048]
    updates = eof_count - (1 if linearized else 0)
    if updates > 1:
        findings.append(Finding(
            Severity.MEDIUM, "edit-history",
            f"The file contains {updates - 1} incremental update(s) appended after the original save.",
            evidence="Content was added or changed after the document was first generated. "
                     "Banks do not normally issue statements that way."))

    producer, creator = meta["/Producer"], meta["/Creator"]
    blob = f"{producer} {creator}".lower()
    hits = [m for m in EDITOR_MARKERS if m in blob]
    if hits:
        findings.append(Finding(
            Severity.MEDIUM, "producer",
            "Producer/Creator metadata names a general-purpose PDF editor or converter.",
            evidence=f"Producer={producer!r}; Creator={creator!r}. "
                     "Compare with a known-genuine statement from the same bank."))

    created = _parse_pdf_date(meta["/CreationDate"])
    modified = _parse_pdf_date(meta["/ModDate"])
    if created and modified and modified - created > timedelta(minutes=2):
        findings.append(Finding(
            Severity.MEDIUM, "dates",
            "Modification date is later than the creation date.",
            evidence=f"created {created.isoformat()}; modified {modified.isoformat()}"))

    if re.search(rb"stEvt:action|xmpMM:History", raw):
        findings.append(Finding(
            Severity.LOW, "xmp-history",
            "Embedded XMP metadata contains an edit-history record.",
            evidence="Inspect the XMP packet to see which application saved it and when."))
    return findings


def _group_words(chars):
    words, cur = [], []
    for ch in chars:
        if ch["text"].isspace():
            if cur:
                words.append(cur)
                cur = []
            continue
        if cur and ch["x0"] - cur[-1]["x1"] > 1.5:
            words.append(cur)
            cur = []
        cur.append(ch)
    if cur:
        words.append(cur)
    return [{
        "text": "".join(c["text"] for c in w),
        "x0": min(c["x0"] for c in w), "x1": max(c["x1"] for c in w),
        "top": min(c["top"] for c in w), "bottom": max(c["bottom"] for c in w),
        "fonts": frozenset((c["fontname"].split("+")[-1], round(c["size"], 1)) for c in w),
    } for w in words]


def extract_lines(path: Path):
    """Return (text, page_of_line, lines, page_sizes) from a PDF text layer."""
    lines, sizes = [], []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            sizes.append((round(float(page.width)), round(float(page.height))))
            for ln in page.extract_text_lines(return_chars=True):
                if ln["text"].strip():
                    lines.append({"page": pno, "text": ln["text"],
                                  "words": _group_words(ln["chars"]), "source": "text"})
    return lines_to_text(lines) + (sizes,)


def lines_to_text(lines):
    text = "\n".join(ln["text"] for ln in lines)
    page_of_line = {i: ln["page"] for i, ln in enumerate(lines, 1)}
    return text, page_of_line, lines


def _theil_sen(xs, ys):
    slopes = [(xs[j] - xs[i]) / (ys[j] - ys[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs)) if abs(ys[j] - ys[i]) > 1e-6]
    slope = median(slopes) if slopes else 0.0
    intercept = median(x - slope * y for x, y in zip(xs, ys))
    return slope, intercept


def alignment_outliers(items, tol):
    """items: list of (key, word). Return [(key, edge, offset)] for words off the column edge.

    Fits a straight line through the aligned edge (so a slightly skewed scan still passes)
    and reports words whose residual exceeds tol.
    """
    if len(items) < 4:
        return []
    best = None
    for edge in ("x1", "x0"):
        xs = [w[edge] for _, w in items]
        ys = [w["top"] for _, w in items]
        slope, icpt = _theil_sen(xs, ys)
        resid = [x - (slope * y + icpt) for x, y in zip(xs, ys)]
        score = median(abs(r) for r in resid)
        if best is None or score < best[0]:
            best = (score, edge, resid)
    _, edge, resid = best
    if sum(abs(r) <= tol for r in resid) / len(resid) < 0.6:
        return []
    return [(k, edge, r) for (k, _), r in zip(items, resid) if abs(r) > tol]


def check_layout(st: ParsedStatement, lines, align_tol: float = 1.0) -> list:
    """Compare fonts (text layer only) and column alignment across transaction rows."""
    findings: list = []
    rows = []
    for t in st.transactions:
        if t.line_no - 1 >= len(lines):
            continue
        ln = lines[t.line_no - 1]
        if len(ln["words"]) >= 3:
            rows.append((t, ln["page"], ln["words"]))
    if len(rows) < 4:
        return findings

    if all("fonts" in w for _, _, ws in rows for w in ws):
        columns = {
            "balance": lambda w: [w[-1]],
            "amount": lambda w: [w[-2]],
            "description": lambda w: w[1:-2],
        }
        for name, pick in columns.items():
            per_row = []
            for t, page, words in rows:
                ks = frozenset().union(*(w["fonts"] for w in pick(words))) if pick(words) else frozenset()
                per_row.append(ks)
            counts = Counter(k for k in per_row if k)
            if not counts:
                continue
            dominant, n = counts.most_common(1)[0]
            share = n / sum(counts.values())
            if share < 0.6:
                continue
            for (t, page, _), keys in zip(rows, per_row):
                if keys and keys != dominant:
                    sev = Severity.HIGH if name != "description" and share >= 0.9 else Severity.MEDIUM
                    fmt = lambda ks: ", ".join(f"{f} {s}pt" for f, s in sorted(ks))
                    findings.append(Finding(
                        sev, "font-mismatch",
                        f"The {name} on this row uses a different font or size from the other rows.",
                        f"line {t.line_no}, page {page}",
                        f"row uses {fmt(keys)}; {share:.0%} of rows use {fmt(dominant)}. Row: {t.raw}"))

    by_page: dict = {}
    for t, page, words in rows:
        by_page.setdefault(page, []).append((t, words[-1]))
    for page, items in by_page.items():
        for t, edge, off in alignment_outliers(items, align_tol):
            findings.append(Finding(
                Severity.MEDIUM, "column-alignment",
                "The balance figure is not aligned with the balance column on other rows.",
                f"line {t.line_no}, page {page}",
                f"{edge} is {off:+.1f} units off the column line. Retyped numbers often shift. "
                f"Row: {t.raw}"))
    return findings
