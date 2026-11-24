"""Command line.

    python -m stmtcheck statement.pdf [--json] [--profile bank.json] [--no-ocr]
    python -m stmtcheck learn genuine1.pdf genuine2.pdf --name "My Bank" -o bank.json
    python -m stmtcheck serve [--host 127.0.0.1] [--port 8000]
"""
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


def _check(argv) -> int:
    p = argparse.ArgumentParser(prog="stmtcheck",
                                description="Screen a bank statement for signs of tampering.")
    p.add_argument("file", help="statement: .pdf, image (.png/.jpg/.tif...), or .txt of OCR output")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--profile", help="bank profile JSON created with 'stmtcheck learn'")
    p.add_argument("--no-ocr", action="store_true", help="skip OCR of scanned documents")
    p.add_argument("--ela-dir", help="write error-level-analysis heatmaps of scanned pages here")
    args = p.parse_args(argv)
    try:
        result = analyze(args.file, profile=args.profile, ocr=not args.no_ocr, ela_dir=args.ela_dir)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.json:
        payload = dict(result, top_severity=result["top_severity"].name,
                       findings=[f.to_dict() for f in result["findings"]], disclaimer=DISCLAIMER)
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(result))
    top = result["top_severity"]
    return 2 if top >= Severity.HIGH else 1 if top >= Severity.MEDIUM else 0


def _learn(argv) -> int:
    from .profiles import learn_profile, save_profile
    p = argparse.ArgumentParser(prog="stmtcheck learn",
                                description="Build a bank profile from known-genuine statement PDFs.")
    p.add_argument("pdfs", nargs="+", help="two or more genuine statements from the same issuer")
    p.add_argument("--name", required=True)
    p.add_argument("-o", "--output", required=True)
    args = p.parse_args(argv)
    profile = learn_profile(args.pdfs, args.name)
    save_profile(profile, args.output)
    print(f"Saved profile {args.name!r} from {profile['samples']} sample(s) to {args.output}")
    if profile["samples"] < 2:
        print("warning: one sample cannot identify boilerplate lines; use at least two.",
              file=sys.stderr)
    return 0


def _serve(argv) -> int:
    from .web import serve
    p = argparse.ArgumentParser(prog="stmtcheck serve", description="Run the local web UI.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "learn":
        return _learn(argv[1:])
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    return _check(argv)


if __name__ == "__main__":
    sys.exit(main())
