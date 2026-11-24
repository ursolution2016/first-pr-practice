"""PDF-level checks: edit history, producer metadata, font and alignment consistency."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from .models import Finding, ParsedStatement, Severity

# Tools people use to edit an existing PDF, or that re-render page content.
EDITOR_MARKERS = [
    "acrobat pro", "adobe acrobat", "photoshop", "illustrator", "indesign", "foxit", "phantompdf",
    "pdfescape", "sejda", "ilovepdf", "smallpdf", "pdf-xchange", "nitro", "pdfelement",
    "wondershare", "canva", "microsoft word", "libreoffice", "openoffice", "skia/pdf",
    "quartz pdfcontext", "pdf-lib", "pdfsam", "pdftk", "ghostscript", "pdf24", "soda pdf",
    "pdfcandy", "docfly", "dochub", "lightpdf", "online2pdf",
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


def check_metadata(path: Path) -> list:
    findings: list = []
    raw = path.read_bytes()
    reader = PdfReader(str(path))
    meta = reader.metadata or {}

    eof_count = len(re.findall(rb"%%EOF", raw))
    linearized = b"/Linearized" in raw[:2048]
    updates = eof_count - (1 if linearized else 0)
    if updates > 1:
        findings.append(Finding(
            Severity.MEDIUM, "edit-history",
            f"The file contains {updates - 1} incremental update(s) appended after the original save.",
            evidence="Content was added or changed after the document was first generated. "
                     "Banks do not normally issue statements that way."))

    producer = str(meta.get("/Producer", "") or "")
    creator = str(meta.get("/Creator", "") or "")
    blob = f"{producer} {creator}".lower()
    hits = [m for m in EDITOR_MARKERS if m in blob]
    if hits:
        findings.append(Finding(
            Severity.MEDIUM, "producer",
            "Producer/Creator metadata names a general-purpose PDF editor or converter.",
            evidence=f"Producer={producer!r}; Creator={creator!r}. "
                     "Compare with a known-genuine statement from the same bank."))

    created = _parse_pdf_date(meta.get("/CreationDate"))
    modified = _parse_pdf_date(meta.get("/ModDate"))
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


def _words(chars):
    """Group a line's chars into words."""
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
    return words


def _font_keys(chars):
    return frozenset((c["fontname"].split("+")[-1], round(c["size"], 1)) for c in chars)


def extract_lines(path: Path):
    """Return (text, page_of_line, lines) where lines[i] = (page, text, chars)."""
    lines = []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for ln in page.extract_text_lines(return_chars=True):
                if ln["text"].strip():
                    lines.append((pno, ln["text"], ln["chars"]))
    text = "\n".join(t for _, t, _ in lines)
    page_of_line = {i: p for i, (p, _, _) in enumerate(lines, 1)}
    return text, page_of_line, lines


def check_layout(st: ParsedStatement, lines) -> list:
    """Compare fonts and column alignment across transaction rows."""
    findings: list = []
    rows = []
    for t in st.transactions:
        if t.line_no - 1 >= len(lines):
            continue
        page, _, chars = lines[t.line_no - 1]
        words = _words(chars)
        if len(words) < 3:
            continue
        rows.append((t, page, words))
    if len(rows) < 4:
        return findings

    def fonts_of(word):
        return _font_keys(word)

    # Font consistency per column: description (all words before the last two), amount, balance.
    columns = {
        "balance": lambda w: [w[-1]],
        "amount": lambda w: [w[-2]],
        "description": lambda w: w[1:-2],
    }
    for name, pick in columns.items():
        per_row = []
        for t, page, words in rows:
            chars = [c for w in pick(words) for c in w]
            per_row.append(_font_keys(chars) if chars else frozenset())
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

    # Column alignment of the balance figure: the aligned edge should be identical on every row.
    by_page: dict = {}
    for t, page, words in rows:
        by_page.setdefault(page, []).append((t, words[-1]))
    for page, items in by_page.items():
        if len(items) < 4:
            continue
        best = None
        for edge in ("x1", "x0"):
            vals = [round(w[-1][edge] * 2) / 2 if edge == "x1" else round(w[0][edge] * 2) / 2
                    for _, w in items]
            mode, n = Counter(vals).most_common(1)[0]
            if best is None or n > best[2]:
                best = (edge, mode, n, vals)
        edge, mode, n, vals = best
        if n / len(items) < 0.6:
            continue
        for (t, _), v in zip(items, vals):
            if abs(v - mode) > 1.0:
                findings.append(Finding(
                    Severity.MEDIUM, "column-alignment",
                    "The balance figure is not aligned with the balance column on other rows.",
                    f"line {t.line_no}, page {page}",
                    f"{edge} = {v}pt versus column at {mode}pt. Retyped numbers often shift. Row: {t.raw}"))
    return findings
