"""Local web UI: Check a statement, manage the Reference library, read About.

Check uploads are written to a private temporary directory, analysed and deleted straight away.
The Reference library (originals you add on purpose) is stored under ~/.stmtcheck, owner-only.
Analysis results are kept in memory for 20 minutes so the export buttons work, then dropped.

Binds to 127.0.0.1 by default. Put authentication and TLS in front of it before exposing it to
anyone else: statements are sensitive. Forms carry a per-process CSRF token.
"""
from __future__ import annotations

import hmac
import json
import secrets
import tempfile
import time
from html import escape
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

from .analyze import SUPPORTED, analyze, public_view
from .cli import DISCLAIMER
from .library import Library
from .report import build_export, to_csv, to_pdf

RESULT_TTL = 20 * 60
MAX_RESULTS = 25

CSS = """
:root{--bg:#f7f7f5;--fg:#1c1c1a;--card:#fff;--line:#d9d9d4;--muted:#66665f;--hi:#b3261e;--med:#a15c00;--low:#2d6a4f;--info:#4a5568;--accent:#1f4e79;--ok:#2d6a4f}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--card:#20201f;--line:#38383a;--muted:#a0a09a;--hi:#ff8a80;--med:#ffb74d;--low:#81c995;--info:#aab4c3;--accent:#8ab4f8;--ok:#81c995}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
nav{background:var(--card);border-bottom:1px solid var(--line);display:flex;gap:4px;padding:8px 12px;flex-wrap:wrap;position:sticky;top:0}
nav .brand{font-weight:700;margin-right:12px;align-self:center}
nav a{color:var(--fg);text-decoration:none;padding:8px 14px;border-radius:8px}
nav a.on{background:var(--accent);color:#fff}nav a:hover:not(.on){background:var(--bg)}
main{max-width:900px;margin:0 auto;padding:20px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:0 0 8px}p.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
label{display:block;font-weight:600;margin:12px 0 4px}input[type=text],select,textarea{width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
input[type=file]{width:100%}
button,.btn{margin-top:14px;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:1rem;cursor:pointer;text-decoration:none;display:inline-block}
button.sec{background:var(--card);color:var(--fg);border:1px solid var(--line)}button.danger{background:var(--hi);color:#fff}
.f{border-left:4px solid var(--line);padding:8px 12px;margin:10px 0;background:var(--bg);border-radius:0 6px 6px 0}
.HIGH{border-color:var(--hi)}.MEDIUM{border-color:var(--med)}.LOW{border-color:var(--low)}.INFO{border-color:var(--info)}
.tag{font-size:.75rem;font-weight:700;letter-spacing:.04em}.HIGH .tag{color:var(--hi)}.MEDIUM .tag{color:var(--med)}.LOW .tag{color:var(--low)}.INFO .tag{color:var(--info)}
.ev{color:var(--muted);font-size:.9rem;word-break:break-word}.verdict{font-size:1.2rem;font-weight:700}
.err{color:var(--hi)}.ok{color:var(--ok)}small,.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;word-break:break-word}
.scroll{overflow-x:auto}.row{display:flex;gap:8px;flex-wrap:wrap}.row form{margin:0}
"""

NAV = [("/", "Check statement"), ("/library", "Reference library"), ("/about", "About")]


def _layout(active: str, title: str, body: str) -> str:
    links = "".join(f'<a href="{h}"{" class=on" if h == active else ""}>{escape(t)}</a>' for h, t in NAV)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)} - Statement Check</title><style>{CSS}</style></head><body>'
            f'<nav><span class="brand">Statement Check</span>{links}</nav>'
            f'<main>{body}<p><small>{escape(DISCLAIMER)}</small></p></main></body></html>')


def _findings_html(findings) -> str:
    items = []
    for f in findings:
        d = f.to_dict()
        items.append(
            f'<div class="f {d["severity"]}"><span class="tag">{d["severity"]}</span> '
            f'<strong>{escape(d["check"])}</strong>: {escape(d["message"])}'
            + (f'<div class="ev">Where: {escape(d["location"])}</div>' if d["location"] else "")
            + (f'<div class="ev">{escape(d["evidence"])}</div>' if d["evidence"] else "") + "</div>")
    return "".join(items) or "<p>No findings.</p>"


def _diff_table(comp) -> str:
    if not comp["diffs"]:
        return '<p class="ok">Every figure matches the stored original.</p>'
    rows = "".join(
        f"<tr><td>{escape(d['kind'])}</td><td>{escape(d['field'])}</td><td>{escape(d['row'])}</td>"
        f"<td>{escape(d['submitted'])}</td><td>{escape(d['original'])}</td></tr>" for d in comp["diffs"])
    return ('<div class="scroll"><table><tr><th>Kind</th><th>Field</th><th>Row</th><th>Submitted</th>'
            f'<th>Original</th></tr>{rows}</table></div>')


def create_app(library_root=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    lib = Library(library_root)
    csrf = secrets.token_hex(16)
    app.csrf_token = csrf
    store: dict = {}

    def hidden() -> str:
        return f'<input type="hidden" name="csrf" value="{csrf}">'

    @app.before_request
    def guard():
        if request.method == "POST" and request.path != "/api/analyze":
            if not hmac.compare_digest(request.form.get("csrf", ""), csrf):
                abort(403)

    def keep(result) -> str:
        now = time.time()
        for k in [k for k, v in store.items() if now - v["t"] > RESULT_TTL]:
            del store[k]
        while len(store) >= MAX_RESULTS:
            del store[min(store, key=lambda k: store[k]["t"])]
        token = secrets.token_urlsafe(16)
        store[token] = {"t": now, "result": result}
        return token

    def run_analysis(upload, profile_file, against):
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in SUPPORTED:
            return None, f"Unsupported file type {suffix or '(none)'}.", 400
        profile = None
        if profile_file and profile_file.filename:
            try:
                profile = json.loads(profile_file.read(1_000_000).decode("utf-8"))
            except Exception:
                return None, "The profile is not valid JSON.", 400
        with tempfile.TemporaryDirectory(prefix="stmtcheck-") as tmp:
            target = Path(tmp) / f"upload{suffix}"  # never use the client's filename on disk
            upload.save(target)
            try:
                result = analyze(target, profile=profile, library=lib,
                                 against=against if against != "none" else None)
            except ValueError as exc:
                return None, str(exc), 400
            except Exception as exc:
                app.logger.warning("analysis failed: %s", type(exc).__name__)
                return None, "That file could not be analysed. It may be corrupt or unsupported.", 422
        result["file"] = Path(upload.filename).name
        return result, None, 200

    # ------------------------------------------------------------------ Check
    def check_page(msg=""):
        opts = ['<option value="auto">Find the best match in my library automatically</option>',
                '<option value="none">Do not compare</option>']
        opts += [f'<option value="{r["id"]}">{escape(r["label"])}</option>' for r in lib.list()]
        body = (f'<h1>Check a statement</h1><p class="sub">Screen a statement for signs of alteration. '
                f'The upload is analysed and deleted immediately.</p>{msg}'
                f'<form class="card" method="post" action="/analyze" enctype="multipart/form-data">{hidden()}'
                '<label for="file">Statement (PDF, image or .txt)</label>'
                '<input id="file" name="file" type="file" required '
                'accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt">'
                f'<label for="against">Compare with a stored original</label>'
                f'<select id="against" name="against">{"".join(opts)}</select>'
                '<label for="profile">Bank profile (optional, JSON from <code>stmtcheck learn</code>)</label>'
                '<input id="profile" name="profile" type="file" accept=".json">'
                '<button type="submit">Check statement</button></form>')
        if not lib.list():
            body += ('<p class="muted">Your Reference library is empty. Add known-genuine originals under '
                     '<a href="/library">Reference library</a> to enable row-by-row and layout comparison.</p>')
        return _layout("/", "Check", body)

    @app.get("/")
    def index():
        return check_page()

    def results_page(result, token):
        comp, ref = result["comparison"], result["reference"]
        body = (f'<h1>Result</h1><section class="card"><div class="verdict">{escape(result["verdict"])}</div>'
                f'<small>{escape(result["file"])} - {result["rows_checked"]} transaction row(s) checked</small>')
        if ref:
            body += (f'<p>Compared with stored original <strong>{escape(ref["label"])}</strong> '
                     f'(added {escape(ref["added_at"])}).</p>')
        body += "</section>"
        if comp and comp["same_statement"] and not comp["identical_file"]:
            body += (f'<section class="card"><h2>Differences from the original</h2>'
                     f'{_diff_table(comp)}</section>')
        body += f'<section class="card"><h2>Findings</h2>{_findings_html(result["findings"])}</section>'

        # Corrections: only against a verified source.
        body += (f'<section class="card"><h2>Correct and export</h2>'
                 f'<form method="post" action="/export/{token}">{hidden()}')
        if comp and comp["same_statement"] and not comp["identical_file"] and comp["diffs"]:
            n = sum(1 for d in comp["diffs"] if d["kind"] == "changed")
            body += (f'<p>Every figure that differs will be <strong>restored to the value in your stored '
                     f'original</strong> ({n} changed value(s); {sum(d["kind"] == "missing" for d in comp["diffs"])}'
                     f' missing row(s) reinserted; {sum(d["kind"] == "added" for d in comp["diffs"])} extra '
                     'row(s) excluded). Each change is marked in the output.</p>')
        elif result["ocr_suggestions"]:
            body += ('<p>Without a stored original, the only corrections available are these possible '
                     'OCR misreads. Check each against the image and tick only those you confirm:</p>'
                     '<table><tr><th></th><th>Row</th><th>Field</th><th>Read as</th><th>Suggested</th></tr>')
            for s in result["ocr_suggestions"]:
                body += (f'<tr><td><input type="checkbox" name="fix" value="{escape(s["id"])}"></td>'
                         f'<td>{s["line_no"] or "opening"}</td><td>{escape(s["field"])}</td>'
                         f'<td>{escape(s["submitted"])}</td><td>{escape(s["suggested"])}</td></tr>')
            body += "</table>"
        else:
            body += ('<p>There is no verified source to correct against, so figures are exported exactly '
                     'as submitted and rows that do not reconcile are marked <strong>UNVERIFIED</strong>. '
                     'Balances are not recalculated to make the statement add up: that would only hide '
                     'a problem, because it cannot show which figure is wrong. To correct this statement, '
                     'add the genuine original to the <a href="/library">Reference library</a> and re-run, '
                     'or obtain a certified copy from the issuing bank.</p>')
        body += ('<div class="row"><button name="fmt" value="pdf" type="submit">Download report (PDF)</button>'
                 '<button class="sec" name="fmt" value="csv" type="submit">Download data (CSV)</button>'
                 '</div></form></section>')
        return _layout("/", "Result", body)

    @app.post("/analyze")
    def analyze_form():
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return check_page('<p class="err">No file uploaded.</p>'), 400
        result, err, code = run_analysis(upload, request.files.get("profile"),
                                         request.form.get("against", "auto"))
        if err:
            return check_page(f'<p class="err">{escape(err)}</p>'), code
        return results_page(result, keep(result))

    @app.post("/export/<token>")
    def export(token):
        entry = store.get(token)
        if not entry or time.time() - entry["t"] > RESULT_TTL:
            return _layout("/", "Expired", '<h1>Result expired</h1><p>Please run the check again.</p>'), 410
        result = entry["result"]
        export_ = build_export(result, request.form.getlist("fix"))
        stem = Path(result["file"]).stem[:60] or "statement"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
        if request.form.get("fmt") == "csv":
            return Response(to_csv(result, export_), mimetype="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{safe}-corrected.csv"'})
        return Response(to_pdf(result, export_), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{safe}-reconciliation.pdf"'})

    # ---------------------------------------------------------------- Library
    def library_page(msg=""):
        refs = lib.list()
        rows = ""
        for r in refs:
            warn = (f' <span class="err">(scores {r["self_check"]} on the checks - verify it)</span>'
                    if r["self_check"] in ("HIGH", "MEDIUM") else "")
            rows += (f'<tr><td><strong>{escape(r["label"])}</strong>{warn}<br>'
                     f'<small>{escape(r["issuer"] or "")} {escape(r["notes"] or "")}</small></td>'
                     f'<td>{r["rows"]}</td><td>{escape(r["added_at"])}</td>'
                     f'<td><form method="post" action="/library/{r["id"]}/delete" '
                     f'onsubmit="return confirm(\'Remove this original from the library?\')">{hidden()}'
                     '<button class="danger" type="submit" style="margin:0;padding:6px 12px">Remove</button>'
                     '</form></td></tr>')
        table = ('<div class="scroll"><table><tr><th>Original</th><th>Rows</th><th>Added</th><th></th></tr>'
                 f'{rows}</table></div>' if refs else '<p class="muted">No originals stored yet.</p>')
        body = (f'<h1>Reference library</h1><p class="sub">Known-genuine originals kept for comparing future '
                f'statements, row by row and line by line.</p>{msg}'
                f'<form class="card" method="post" action="/library/add" enctype="multipart/form-data">{hidden()}'
                '<h2>Add an original</h2>'
                '<label for="rf">Original statement (PDF, image or .txt)</label>'
                '<input id="rf" name="file" type="file" required '
                'accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt">'
                '<label for="lbl">Label (required)</label>'
                '<input id="lbl" name="label" type="text" maxlength="120" required placeholder="e.g. Everyday account, March 2025">'
                '<label for="iss">Issuer (optional)</label><input id="iss" name="issuer" type="text" maxlength="120">'
                '<label for="nts">Notes (optional)</label><textarea id="nts" name="notes" rows="2" maxlength="500"></textarea>'
                '<p class="muted">Only add documents you obtained directly from the issuer or otherwise know '
                'to be genuine: everything else is judged against these. Files are stored privately on this '
                'machine.</p><button type="submit">Add to library</button></form>'
                f'<section class="card"><h2>Stored originals ({len(refs)})</h2>{table}</section>')
        return _layout("/library", "Reference library", body)

    @app.get("/library")
    def library_view():
        return library_page()

    @app.post("/library/add")
    def library_add():
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return library_page('<p class="err">No file uploaded.</p>'), 400
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in SUPPORTED:
            return library_page(f'<p class="err">Unsupported file type {escape(suffix)}.</p>'), 400
        with tempfile.TemporaryDirectory(prefix="stmtcheck-") as tmp:
            target = Path(tmp) / f"upload{suffix}"
            upload.save(target)
            try:
                ref = lib.add(target, request.form.get("label", ""), request.form.get("issuer", ""),
                              request.form.get("notes", ""), original_name=Path(upload.filename).name)
            except ValueError as exc:
                return library_page(f'<p class="err">{escape(str(exc))}</p>'), 400
            except Exception as exc:
                app.logger.warning("library add failed: %s", type(exc).__name__)
                return library_page('<p class="err">That file could not be read.</p>'), 422
        msg = f'<p class="ok">Stored "{escape(ref["label"])}" ({ref["rows"]} rows read).</p>'
        if ref["self_check"] in ("HIGH", "MEDIUM"):
            msg += ('<p class="err">Warning: this original itself scores '
                    f'{escape(ref["self_check"])} on the checks. Confirm it is genuine (or an OCR misread) '
                    'before relying on it.</p>')
        if ref["rows"] == 0:
            msg += '<p class="err">No transaction rows could be read, so it cannot be used for comparison.</p>'
        return library_page(msg)

    @app.post("/library/<rid>/delete")
    def library_delete(rid):
        ok = lib.remove(rid) if lib.get(rid) else False
        return library_page('<p class="ok">Removed.</p>' if ok else '<p class="err">No such original.</p>')

    # ------------------------------------------------------------------ About
    @app.get("/about")
    def about():
        body = ('<h1>About</h1><div class="card"><h2>What this does</h2>'
                '<p>Screens a bank statement for signs of alteration: arithmetic that does not reconcile, '
                'retyped figures, edit history, scan artefacts, and differences from a stored genuine '
                'original.</p><h2>Corrections</h2><p>Figures are only corrected against a verified source '
                '(a stored original, or an OCR misread you confirm). Balances are never recalculated to make a '
                'document add up, and exports are marked as derived data, not a bank statement.</p>'
                '<h2>Limits</h2><p>Findings are indicators, not proof. Someone who regenerates a whole '
                'document with consistent fonts and correct arithmetic can pass the arithmetic, layout and '
                'metadata checks; only a comparison with a genuine original, or confirmation from the issuing '
                'bank, settles it. OCR can misread digits.</p></div>')
        return _layout("/about", "About", body)

    # -------------------------------------------------------------------- API
    @app.post("/api/analyze")
    def analyze_api():
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify(error="No file uploaded."), 400
        result, err, code = run_analysis(upload, request.files.get("profile"),
                                         request.form.get("reference", "none"))
        if err:
            return jsonify(error=err), code
        return jsonify(dict(public_view(result), file=result["file"], disclaimer=DISCLAIMER))

    @app.errorhandler(413)
    def too_big(_):
        return jsonify(error="File too large (limit 25 MB)."), 413

    return app


def serve(host: str = "127.0.0.1", port: int = 8000, library_root=None) -> None:
    create_app(library_root).run(host=host, port=port, debug=False)
