"""Small local web UI: upload a statement, get the tamper-screening report.

Uploads are written to a private temporary directory, analysed, and deleted straight away.
Nothing is stored or logged. Binds to 127.0.0.1 by default; put it behind authentication and
TLS before exposing it to anyone else.
"""
from __future__ import annotations

import json
import tempfile
from html import escape
from pathlib import Path

from flask import Flask, jsonify, request

from .analyze import SUPPORTED, analyze
from .cli import DISCLAIMER

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Statement Check</title>
<style>
:root{--bg:#f7f7f5;--fg:#1c1c1a;--card:#fff;--line:#d9d9d4;--muted:#66665f;--hi:#b3261e;--med:#a15c00;--low:#2d6a4f;--info:#4a5568;--accent:#1f4e79}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--card:#20201f;--line:#38383a;--muted:#a0a09a;--hi:#ff8a80;--med:#ffb74d;--low:#81c995;--info:#aab4c3;--accent:#8ab4f8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
label{display:block;font-weight:600;margin:12px 0 4px}input[type=file]{width:100%}
button{margin-top:16px;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:1rem;cursor:pointer}
.f{border-left:4px solid var(--line);padding:8px 12px;margin:10px 0;background:var(--bg);border-radius:0 6px 6px 0}
.HIGH{border-color:var(--hi)}.MEDIUM{border-color:var(--med)}.LOW{border-color:var(--low)}.INFO{border-color:var(--info)}
.tag{font-size:.75rem;font-weight:700;letter-spacing:.04em}.HIGH .tag{color:var(--hi)}.MEDIUM .tag{color:var(--med)}.LOW .tag{color:var(--low)}.INFO .tag{color:var(--info)}
.ev{color:var(--muted);font-size:.9rem;word-break:break-word}.verdict{font-size:1.2rem;font-weight:700}
.err{color:var(--hi)}small{color:var(--muted)}
</style></head><body><main>
<h1>Statement Check</h1>
<p class="sub">Screen a bank statement for signs of alteration. Files are analysed and deleted immediately.</p>
<form class="card" method="post" action="/analyze" enctype="multipart/form-data">
<label for="file">Statement (PDF, image or .txt)</label>
<input id="file" name="file" type="file" required accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt">
<label for="profile">Bank profile (optional, JSON from <code>stmtcheck learn</code>)</label>
<input id="profile" name="profile" type="file" accept=".json">
<button type="submit">Check statement</button></form>
<!--RESULT-->
<p><small><!--DISCLAIMER--></small></p>
</main></body></html>"""


def _page(result_html: str = "") -> str:
    # Substitute the disclaimer first and the (escaped) result last so inserted text is never re-scanned.
    return PAGE.replace("<!--DISCLAIMER-->", escape(DISCLAIMER)).replace("<!--RESULT-->", result_html)


def _render(result: dict) -> str:
    items = []
    for f in result["findings"]:
        d = f.to_dict()
        items.append(
            f'<div class="f {d["severity"]}"><span class="tag">{d["severity"]}</span> '
            f'<strong>{escape(d["check"])}</strong>: {escape(d["message"])}'
            + (f'<div class="ev">Where: {escape(d["location"])}</div>' if d["location"] else "")
            + (f'<div class="ev">{escape(d["evidence"])}</div>' if d["evidence"] else "")
            + "</div>")
    body = "".join(items) or "<p>No findings.</p>"
    return (f'<section class="card"><div class="verdict">{escape(result["verdict"])}</div>'
            f'<small>{result["rows_checked"]} transaction row(s) checked</small>{body}</section>')


def _run(app: Flask):
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return None, "No file uploaded.", 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED:
        return None, f"Unsupported file type {suffix or '(none)'}.", 400
    profile = None
    pf = request.files.get("profile")
    if pf and pf.filename:
        try:
            profile = json.loads(pf.read(1_000_000).decode("utf-8"))
        except Exception:
            return None, "The profile is not valid JSON.", 400
    with tempfile.TemporaryDirectory(prefix="stmtcheck-") as tmp:
        target = Path(tmp) / f"upload{suffix}"  # never use the client's filename
        upload.save(target)
        try:
            result = analyze(target, profile=profile)
        except Exception as exc:  # unreadable or corrupt files
            app.logger.warning("analysis failed: %s", type(exc).__name__)
            return None, "That file could not be analysed. It may be corrupt or unsupported.", 422
    result["file"] = Path(upload.filename).name
    return result, None, 200


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

    @app.get("/")
    def index():
        return _page()

    @app.post("/analyze")
    def analyze_form():
        result, err, code = _run(app)
        if err:
            return _page(f'<p class="err">{escape(err)}</p>'), code
        return _page(_render(result))

    @app.post("/api/analyze")
    def analyze_api():
        result, err, code = _run(app)
        if err:
            return jsonify(error=err), code
        return jsonify(
            file=result["file"], rows_checked=result["rows_checked"], verdict=result["verdict"],
            top_severity=result["top_severity"].name,
            findings=[f.to_dict() for f in result["findings"]], disclaimer=DISCLAIMER)

    @app.errorhandler(413)
    def too_big(_):
        return jsonify(error="File too large (limit 25 MB)."), 413

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    create_app().run(host=host, port=port, debug=False)
