"""Android code paths exercised on the desktop with a fake ML Kit recognizer and fake PDF renderer.

The real recognizer runs only on a phone (see android/ci/smoke.sh); this checks everything around it:
clustering words into rows, normalising figures, confidence handling and the arithmetic checks on top.
"""
import io
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar

from stmtcheck import analyze, androidbridge, ocr
from stmtcheck.models import Severity
from stmtcheck.web import create_app
from test_stmtcheck import ROWS, build_pdf, checks


def words_from_pdf(pdf: Path, scale: float = 2.0, jitter=None) -> list:
    """What a recognizer would return for a statement PDF: one word box per printed token."""
    page = next(extract_pages(str(pdf), laparams=None))
    chars = []

    def walk(o):
        if isinstance(o, LTChar):
            chars.append(o)
        elif hasattr(o, "__iter__"):
            for c in o:
                walk(c)
    walk(page)
    chars.sort(key=lambda c: (round(page.height - c.y1), c.x0))
    words, cur = [], []
    for c in chars + [None]:
        if c is not None and cur and not c.get_text().isspace() and abs(c.y1 - cur[-1].y1) < 2 \
                and c.x0 - cur[-1].x1 < 1.5:
            cur.append(c)
            continue
        if cur:
            words.append({"text": "".join(x.get_text() for x in cur), "x0": min(x.x0 for x in cur) * scale,
                          "x1": max(x.x1 for x in cur) * scale,
                          "top": (page.height - max(x.y1 for x in cur)) * scale,
                          "bottom": (page.height - min(x.y0 for x in cur)) * scale, "conf": 97.0})
        cur = [c] if c is not None and not c.get_text().isspace() else []
    return words


@pytest.fixture()
def fake_android(monkeypatch):
    state = {"words": []}
    monkeypatch.setattr(androidbridge, "available", lambda: True)
    monkeypatch.setattr(androidbridge, "recognize", lambda img: [dict(w) for w in state["words"]])
    return state


def _png(path: Path) -> Path:
    Image.new("RGB", (1200, 1700), "white").save(path)
    return path


def test_ocr_available_on_android(fake_android):
    assert ocr.ocr_available()


def test_words_cluster_into_rows_and_fix_decimal_commas():
    words = [{"text": t, "x0": x, "x1": x + 40, "top": y, "bottom": y + 20, "conf": 96.0}
             for (t, x, y) in [("01/03/2025", 10, 100), ("Salary", 120, 102), ("2,500,00", 300, 99),
                               ("3,500.00", 420, 101), ("Closing", 10, 200), ("balance", 90, 201),
                               ("3,500.00", 420, 200)]]
    lines = ocr.words_to_lines(words, 1)
    assert [ln["text"] for ln in lines] == ["01/03/2025 Salary 2,500.00 3,500.00", "Closing balance 3,500.00"]


def test_clean_statement_passes_through_android_ocr(tmp_path, fake_android):
    build_pdf(tmp_path / "s.pdf")
    fake_android["words"] = words_from_pdf(tmp_path / "s.pdf")
    r = analyze(_png(tmp_path / "scan.png"))
    assert r["rows_checked"] == len(ROWS) and r["by_ocr"]
    assert r["top_severity"] < Severity.MEDIUM, [f.message for f in r["findings"]]


def test_tampered_statement_is_flagged_through_android_ocr(tmp_path, fake_android):
    build_pdf(tmp_path / "t.pdf", balance_override={2: Decimal("2000.00")})
    fake_android["words"] = words_from_pdf(tmp_path / "t.pdf")
    r = analyze(_png(tmp_path / "scan.png"))
    assert "running-balance" in checks(r) and r["top_severity"] == Severity.HIGH


def test_misread_digit_is_downgraded_with_a_suggestion(tmp_path, fake_android):
    build_pdf(tmp_path / "s.pdf")
    words = words_from_pdf(tmp_path / "s.pdf")
    for w in words:   # the recognizer reads one amount's last digit wrongly
        if w["text"] == "212.15":
            w["text"] = "212.16"
    fake_android["words"] = words
    r = analyze(_png(tmp_path / "scan.png"))
    breaks = [f for f in r["findings"] if f.check == "running-balance"]
    assert breaks and all(f.severity == Severity.MEDIUM for f in breaks)
    assert any(s["suggested"] == "212.15" for s in r["ocr_suggestions"])


def test_pdf_render_falls_back_to_android_renderer(monkeypatch, tmp_path):
    import builtins
    real_import = builtins.__import__

    def no_pdfium(name, *a, **k):
        if name.startswith("pypdfium2"):
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pdfium)
    monkeypatch.setattr(androidbridge, "available", lambda: True)
    seen = {}
    monkeypatch.setattr(androidbridge, "render_pdf", lambda p, d: seen.update(dpi=d) or ["page"])
    assert ocr.render_pdf_pages(tmp_path / "x.pdf", 300) == ["page"]
    assert seen["dpi"] == 200  # capped to keep phone memory in check


# --------------------------------------------- local server hardening for the phone shell
@pytest.fixture()
def phone_app(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("STMTCHECK_INBOX", str(inbox))
    app = create_app(library_root=tmp_path / "lib", access_code="x" * 32, bootstrap_token="t0k3n" * 6)
    return app, app.test_client(), inbox


def test_bootstrap_signs_in_only_with_the_secret(phone_app):
    app, c, _ = phone_app
    assert c.get("/").status_code == 302                    # other apps on the phone get nothing
    assert c.get("/_bootstrap/wrong").status_code == 404
    r = c.get("/_bootstrap/" + "t0k3n" * 6)
    assert r.status_code == 302 and "HttpOnly" in r.headers["Set-Cookie"]
    assert c.get("/").status_code == 200


@pytest.mark.parametrize("nxt,expected", [("/library", "/library"), ("//evil.com", "/"),
                                          ("https://evil.com", "/"), ("/\\evil", "/")])
def test_bootstrap_next_cannot_redirect_off_site(phone_app, nxt, expected):
    _, c, _ = phone_app
    r = c.get("/_bootstrap/" + "t0k3n" * 6, query_string={"next": nxt})
    assert r.headers["Location"].endswith(expected) and not r.headers["Location"].startswith("//")


def test_no_bootstrap_token_means_no_bootstrap(tmp_path):
    c = create_app(library_root=tmp_path / "l", access_code="y" * 20).test_client()
    assert c.get("/_bootstrap/anything").status_code == 404


def test_open_shared_analyses_then_deletes_the_inbox_copy(phone_app, tmp_path):
    _, c, inbox = phone_app
    build_pdf(inbox / "abc123.pdf", balance_override={2: Decimal("2000.00")})
    assert c.get("/open-shared?name=abc123.pdf").status_code == 302      # needs the session first
    c.get("/_bootstrap/" + "t0k3n" * 6)
    r = c.get("/open-shared?name=abc123.pdf&label=March statement.pdf")
    assert r.status_code == 200 and b"STRONG INDICATORS" in r.data and b"March statement.pdf" in r.data
    assert not (inbox / "abc123.pdf").exists()


@pytest.mark.parametrize("name", ["../secret.pdf", "/etc/passwd", "sub/x.pdf", "", "missing.pdf", "x.exe"])
def test_open_shared_rejects_anything_outside_the_inbox(phone_app, tmp_path, name):
    _, c, inbox = phone_app
    (tmp_path / "secret.pdf").write_bytes(b"%PDF")
    (inbox / "x.exe").write_bytes(b"MZ")
    c.get("/_bootstrap/" + "t0k3n" * 6)
    assert c.get("/open-shared", query_string={"name": name}).status_code == 404
