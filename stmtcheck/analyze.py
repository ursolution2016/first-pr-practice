"""Orchestrates all checks for a statement file."""
from __future__ import annotations

from pathlib import Path

from .arithmetic import check_arithmetic
from .models import Finding, Severity
from .parser import parse_statement
from .pdfforensics import check_layout, check_metadata, extract_lines


def analyze(path) -> dict:
    path = Path(path)
    findings: list = []
    lines = None
    if path.suffix.lower() == ".pdf":
        findings += check_metadata(path)
        text, page_of_line, lines = extract_lines(path)
        if not text.strip():
            findings.append(Finding(
                Severity.MEDIUM, "no-text-layer",
                "The PDF has no text layer (scanned or flattened image).",
                evidence="Arithmetic and font checks need text. Run OCR and re-check the output with "
                         "a .txt file. A flattened image of a statement is also a common way to "
                         "hide editing, so treat it with caution."))
    else:
        text, page_of_line = path.read_text(encoding="utf-8", errors="replace"), None

    statement = parse_statement(text, page_of_line)
    findings += check_arithmetic(statement)
    if lines is not None:
        findings += check_layout(statement, lines)

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
