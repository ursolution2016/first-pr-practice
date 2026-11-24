"""Command line: python -m stmtcheck statement.pdf [--json]"""
from __future__ import annotations

import argparse
import json
import sys

from .analyze import analyze
from .models import Severity

DISCLAIMER = (
    "Findings are indicators, not proof. A clean result does not establish authenticity: a careful "
    "forger can keep the arithmetic consistent. Confirm important documents with the issuing bank."
)


def render_text(result: dict) -> str:
    out = [f"File: {result['file']}", f"Rows checked: {result['rows_checked']}",
           f"Verdict: {result['verdict']}", ""]
    if not result["findings"]:
        out.append("No findings.")
    for f in result["findings"]:
        out.append(f"[{f.severity.name}] {f.check}: {f.message}")
        if f.location:
            out.append(f"    where:    {f.location}")
        if f.evidence:
            out.append(f"    evidence: {f.evidence}")
    out += ["", DISCLAIMER]
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stmtcheck",
                                description="Screen a bank statement for signs of tampering.")
    p.add_argument("file", help="statement as .pdf, or .txt of OCR output")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)
    result = analyze(args.file)
    if args.json:
        payload = dict(result, top_severity=result["top_severity"].name,
                       findings=[f.to_dict() for f in result["findings"]], disclaimer=DISCLAIMER)
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(result))
    top = result["top_severity"]
    return 2 if top >= Severity.HIGH else 1 if top >= Severity.MEDIUM else 0


if __name__ == "__main__":
    sys.exit(main())
