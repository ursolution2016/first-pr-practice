"""Tests for OCR, image forensics, bank profiles and the web UI (synthetic data only)."""
import io
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from stmtcheck import analyze
from stmtcheck.arithmetic import _ocr_explains
from stmtcheck.cli import main
from stmtcheck.models import Severity
from stmtcheck.ocr import normalise_money_text, ocr_available, render_pdf_pages
from stmtcheck.profiles import learn_profile, load_profile
from stmtcheck.web import create_app
from test_stmtcheck import ROWS, build_pdf, checks

needs_ocr = pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")


def make_scan(pdf: Path, out: Path, seed=1, sigma=4.0, quality=80, dpi=200):
    page = render_pdf_pages(pdf, dpi)[0]
    a = np.asarray(page, dtype=np.float32) + np.random.default_rng(seed).normal(
        0, sigma, (page.height, page.width, 1))
    img = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    img.save(out, quality=quality)
    return img


@pytest.fixture(scope="module")
def scans(tmp_path_factory):
    d = tmp_path_factory.mktemp("scans")
    build_pdf(d / "clean.pdf")
    build_pdf(d / "tamper.pdf", balance_override={2: Decimal("2000.00")})
    make_scan(d / "clean.pdf", d / "clean.jpg")
    make_scan(d / "tamper.pdf", d / "tamper.jpg")
    return d


# ---------- OCR ----------
def test_normalise_money_text():
    assert normalise_money_text("212,16") == "212.16"
    assert normalise_money_text("1,450.00") == "1,450.00"
    assert normalise_money_text("1,234,56") == "1,234.56"


def test_ocr_confusion_explains_single_misread():
    # 212.15 misread as 212.16: prev 2263.60 - 212.15 = 2051.45 printed correctly.
    assert _ocr_explains("2263.60", "212.16", "2051.45")
    assert not _ocr_explains("2263.60", "212.15", "1000.00")


@needs_ocr
def test_clean_scan_has_no_findings(scans):
    r = analyze(scans / "clean.jpg")
    assert r["top_severity"] < Severity.MEDIUM, [f.message for f in r["findings"]]
    assert r["rows_checked"] == len(ROWS)


@needs_ocr
def test_tampered_scan_is_flagged(scans):
    r = analyze(scans / "tamper.jpg")
    assert "running-balance" in checks(r)
    assert r["top_severity"] == Severity.HIGH


@needs_ocr
def test_flattened_pdf_is_ocrd_and_flagged(scans, tmp_path):
    pdf = tmp_path / "flat.pdf"
    Image.open(scans / "tamper.jpg").convert("RGB").save(pdf, "PDF", resolution=200)
    r = analyze(pdf)
    assert "no-text-layer" in checks(r)
    assert "running-balance" in checks(r)


def test_no_ocr_flag_skips_text(scans):
    r = analyze(scans / "clean.jpg", ocr=False)
    assert r["rows_checked"] == 0


def test_unreadable_row_does_not_cascade(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("Opening balance 100.00\n01/01/2025 Pay 50.00 150.00\n"
                 "02/01/2025 Shop 20.00\n03/01/2025 Shop 10.00 120.00\nClosing balance 120.00\n")
    r = analyze(p)
    assert "unreadable-row" in checks(r)
    assert "running-balance" not in checks(r)


# ---------- image forensics ----------
def test_whiteout_detected_and_clean_scan_quiet(scans, tmp_path):
    clean = Image.open(scans / "clean.jpg").convert("RGB")
    p_clean = tmp_path / "c.png"
    clean.save(p_clean)
    assert "image-noise" not in checks(analyze(p_clean, ocr=False))

    wo = clean.copy()
    ImageDraw.Draw(wo).rectangle((550, 330, 1100, 420), fill=(255, 255, 255))
    p_wo = tmp_path / "w.png"
    wo.save(p_wo)
    r = analyze(p_wo, ocr=False)
    assert "image-noise" in checks(r)
    assert all(f.severity <= Severity.MEDIUM for f in r["findings"] if f.check == "image-noise")


def test_exif_editor_flagged(scans, tmp_path):
    img = Image.open(scans / "clean.jpg").convert("RGB")
    exif = Image.Exif()
    exif[305] = "Adobe Photoshop 25.0"
    p = tmp_path / "e.jpg"
    img.save(p, exif=exif)
    assert "image-software" in checks(analyze(p, ocr=False))


def test_ela_heatmap_written_not_scored(scans, tmp_path):
    r = analyze(scans / "clean.jpg", ocr=False, ela_dir=tmp_path / "ela")
    assert (tmp_path / "ela" / "clean-ela-page1.png").exists()
    assert not any(f.check == "image-ela" for f in r["findings"])


# ---------- profiles ----------
@pytest.fixture(scope="module")
def profile(tmp_path_factory):
    d = tmp_path_factory.mktemp("prof")
    build_pdf(d / "g1.pdf")
    build_pdf(d / "g2.pdf", rows=ROWS[:5])
    prof = learn_profile([d / "g1.pdf", d / "g2.pdf"], "Test Bank")
    return d, prof


def test_learn_profile_contents(profile):
    _, prof = profile
    assert prof["name"] == "Test Bank" and prof["samples"] == 2
    assert any("opening balance" in s for s in prof["static_lines"])
    assert "Helvetica" in prof["fonts"]


def test_genuine_matches_profile(profile):
    d, prof = profile
    build_pdf(d / "g3.pdf", rows=ROWS[:4])
    r = analyze(d / "g3.pdf", profile=prof)
    assert not [f for f in r["findings"] if f.check.startswith("profile")]


def test_profile_flags_foreign_font_and_producer(profile):
    d, prof = profile
    build_pdf(d / "bad.pdf", font_override={1: "Courier"}, producer="Sejda PDF Editor")
    r = analyze(d / "bad.pdf", profile=prof)
    assert "profile-font" in checks(r)
    assert "profile-producer" in checks(r)
    assert r["top_severity"] == Severity.HIGH


def test_profile_cli_roundtrip(profile, tmp_path, capsys):
    d, _ = profile
    out = tmp_path / "bank.json"
    assert main(["learn", str(d / "g1.pdf"), str(d / "g2.pdf"), "--name", "T", "-o", str(out)]) == 0
    assert load_profile(out)["name"] == "T"
    assert main([str(d / "g1.pdf"), "--profile", str(out)]) == 0


def test_bad_profile_version_rejected():
    with pytest.raises(ValueError):
        load_profile({"version": 99})


# ---------- web ----------
@pytest.fixture()
def client(tmp_path):
    app = create_app(library_root=tmp_path / "lib")
    c = app.test_client()
    c.csrf = app.csrf_token
    return c


def _post(client, url, name, data, **extra):
    if not url.startswith("/api/"):
        extra.setdefault("csrf", client.csrf)
    return client.post(url, data={"file": (io.BytesIO(data), name), **extra},
                       content_type="multipart/form-data")


def test_web_index(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Statement Check" in r.data


def test_web_api_flags_tamper(client, tmp_path):
    build_pdf(tmp_path / "t.pdf", balance_override={2: Decimal("2000.00")})
    r = _post(client, "/api/analyze", "my statement.pdf", (tmp_path / "t.pdf").read_bytes())
    body = r.get_json()
    assert r.status_code == 200
    assert body["top_severity"] == "HIGH" and body["file"] == "my statement.pdf"
    assert any(f["check"] == "running-balance" for f in body["findings"])


def test_web_html_escapes_and_renders(client, tmp_path):
    build_pdf(tmp_path / "t.pdf", balance_override={2: Decimal("2000.00")})
    r = _post(client, "/analyze", "x.pdf", (tmp_path / "t.pdf").read_bytes())
    assert r.status_code == 200 and b"STRONG INDICATORS" in r.data and b"<script" not in r.data


def test_web_rejects_bad_type_and_corrupt(client):
    assert _post(client, "/api/analyze", "evil.exe", b"MZ").status_code == 400
    assert _post(client, "/api/analyze", "x.pdf", b"not a pdf").status_code == 422


def test_web_profile_upload(client, profile, tmp_path):
    d, prof = profile
    build_pdf(tmp_path / "bad.pdf", font_override={1: "Courier"})
    r = _post(client, "/api/analyze", "b.pdf", (tmp_path / "bad.pdf").read_bytes(),
              profile=(io.BytesIO(json.dumps(prof).encode()), "p.json"))
    assert any(f["check"] == "profile-font" for f in r.get_json()["findings"])
