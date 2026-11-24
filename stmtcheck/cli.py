"""Command line.

    python -m stmtcheck statement.pdf [--json] [--profile bank.json] [--no-ocr]
            [--against auto|ID] [--export-dir DIR [--accept-ocr-fix ID ...]]
    python -m stmtcheck library add original.pdf --label "Acme Bank Mar 2025" [--issuer X] [--notes N]
    python -m stmtcheck library list | remove ID
    python -m stmtcheck learn genuine1.pdf genuine2.pdf --name "My Bank" -o bank.json
    python -m stmtcheck serve [--host 127.0.0.1] [--port 8000]
    python -m stmtcheck serve --lan [--access-code CODE]   # reachable from a phone on your Wi-Fi

The reference library lives in ~/.stmtcheck (override with --library DIR or STMTCHECK_HOME).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze, public_view
from .models import Severity

DISCLAIMER = (
    "Findings are indicators, not proof. A clean result does not establish authenticity: a careful "
    "forger can keep the arithmetic consistent. Confirm important documents with the issuing bank."
)


def render_text(result: dict) -> str:
    out = [f"File: {result['file']}", f"Rows checked: {result['rows_checked']}",
           f"Verdict: {result['verdict']}"]
    if result.get("reference"):
        out.append(f"Compared with: {result['reference']['label']} "
                   f"(id {result['reference']['id']})")
    out.append("")
    if not result["findings"]:
        out.append("No findings.")
    for f in result["findings"]:
        out.append(f"[{f.severity.name}] {f.check}: {f.message}")
        if f.location:
            out.append(f"    where:    {f.location}")
        if f.evidence:
            out.append(f"    evidence: {f.evidence}")
    sugg = result.get("ocr_suggestions") or []
    if sugg:
        out += ["", "Possible OCR misreads (confirm by eye, then pass --accept-ocr-fix ID):"]
        out += [f"    {s['id']}: {s['submitted']} -> {s['suggested']}" for s in sugg]
    out += ["", DISCLAIMER]
    return "\n".join(out)


def _library(root):
    from .library import Library
    return Library(root)


def _check(argv) -> int:
    p = argparse.ArgumentParser(prog="stmtcheck",
                                description="Screen a bank statement for signs of tampering.")
    p.add_argument("file", help="statement: .pdf, image (.png/.jpg/.tif...), or .txt of OCR output")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--profile", help="bank profile JSON created with 'stmtcheck learn'")
    p.add_argument("--no-ocr", action="store_true", help="skip OCR of scanned documents")
    p.add_argument("--ela-dir", help="write error-level-analysis heatmaps of scanned pages here")
    p.add_argument("--against", help="compare with a stored original: 'auto' or a library id")
    p.add_argument("--library", help="library directory (default ~/.stmtcheck)")
    p.add_argument("--export-dir", help="write corrected data (CSV) and a reconciliation report (PDF)")
    p.add_argument("--accept-ocr-fix", action="append", default=[], metavar="ID",
                   help="apply a suggested OCR correction you have confirmed by eye (repeatable)")
    args = p.parse_args(argv)
    try:
        lib = _library(args.library) if args.against else None
        result = analyze(args.file, profile=args.profile, ocr=not args.no_ocr, ela_dir=args.ela_dir,
                         library=lib, against=args.against)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(dict(public_view(result), disclaimer=DISCLAIMER), indent=2))
    else:
        print(render_text(result))
    if args.export_dir:
        from .report import build_export, to_csv, to_pdf
        out = Path(args.export_dir)
        out.mkdir(parents=True, exist_ok=True)
        export = build_export(result, args.accept_ocr_fix)
        stem = Path(args.file).stem
        (out / f"{stem}-corrected.csv").write_bytes(to_csv(result, export))
        (out / f"{stem}-reconciliation.pdf").write_bytes(to_pdf(result, export))
        print(f"\nWrote {stem}-corrected.csv and {stem}-reconciliation.pdf to {out}", file=sys.stderr)
    top = result["top_severity"]
    return 2 if top >= Severity.HIGH else 1 if top >= Severity.MEDIUM else 0


def _library_cmd(argv) -> int:
    p = argparse.ArgumentParser(prog="stmtcheck library",
                                description="Manage stored originals used for comparison.")
    p.add_argument("--library", help="library directory (default ~/.stmtcheck)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="store a known-genuine original")
    a.add_argument("file")
    a.add_argument("--label", required=True)
    a.add_argument("--issuer", default="")
    a.add_argument("--notes", default="")
    sub.add_parser("list")
    r = sub.add_parser("remove")
    r.add_argument("id")
    args = p.parse_args(argv)
    lib = _library(args.library)
    try:
        if args.cmd == "add":
            ref = lib.add(args.file, args.label, args.issuer, args.notes)
            print(f"Stored {ref['label']!r} as {ref['id']} ({ref['rows']} rows)")
            if ref["self_check"] in ("HIGH", "MEDIUM"):
                print(f"warning: this original itself scores {ref['self_check']} on the checks. "
                      "Make sure it is genuine (or an OCR misread) before relying on it.",
                      file=sys.stderr)
        elif args.cmd == "list":
            for r in lib.list():
                print(f"{r['id']}  {r['added_at']}  {r['rows']:>3} rows  {r['label']}"
                      + (f"  [{r['issuer']}]" if r["issuer"] else ""))
        else:
            ok = lib.remove(args.id)
            print("Removed." if ok else "No such id.")
            return 0 if ok else 1
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


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
    p.add_argument("--library", help="library directory (default ~/.stmtcheck)")
    p.add_argument("--lan", action="store_true",
                   help="listen on the local network (for a phone) behind an access code")
    p.add_argument("--access-code", help="code required to use the app (generated with --lan if omitted; "
                   "or set STMTCHECK_ACCESS_CODE)")
    args = p.parse_args(argv)
    host, code = args.host, args.access_code
    if args.lan:
        import secrets
        import socket
        host = "0.0.0.0"
        code = code or "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface
                ip = s.getsockname()[0]
            except OSError:
                ip = "<this computer's IP>"
        print(f"On your phone (same Wi-Fi) open  http://{ip}:{args.port}\n"
              f"Access code: {code}\n"
              "This is plain HTTP: use a trusted network only. Press Ctrl+C to stop.", flush=True)
    try:
        serve(host, args.port, args.library, code)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "learn":
        return _learn(argv[1:])
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    if argv and argv[0] == "library":
        return _library_cmd(argv[1:])
    return _check(argv)


if __name__ == "__main__":
    sys.exit(main())
