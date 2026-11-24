"""Reference library, comparison with originals, corrections/export, and the web menu."""
import io
import re
import shutil
from decimal import Decimal

import pytest
from pypdf import PdfReader

from stmtcheck import analyze
from stmtcheck.arithmetic import check_arithmetic
from stmtcheck.cli import main
from stmtcheck.library import Library
from stmtcheck.models import Severity
from stmtcheck.parser import parse_statement
from stmtcheck.report import build_export, to_csv, to_pdf
from stmtcheck.web import create_app
from test_stmtcheck import ROWS, build_pdf, checks

# A *consistent* forgery: the rent is changed and every later balance and total is recomputed,
# so no arithmetic check can notice. Only the stored original can.
FORGED_ROWS = list(ROWS)
FORGED_ROWS[2] = ("05/03/2025", "Rent March", "145.00", Decimal("-145.00"))


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "lib")


@pytest.fixture()
def files(tmp_path):
    build_pdf(tmp_path / "original.pdf")
    build_pdf(tmp_path / "forged.pdf", rows=FORGED_ROWS)
    build_pdf(tmp_path / "other.pdf", rows=[("02/06/2025", "Unrelated shop", "9.99", Decimal("-9.99")),
                                           ("03/06/2025", "Another shop", "5.00", Decimal("-5.00")),
                                           ("04/06/2025", "Third shop", "4.00", Decimal("-4.00")),
                                           ("05/06/2025", "Fourth shop", "3.00", Decimal("-3.00"))])
    return tmp_path


# ------------------------------------------------------------------ library
def test_library_add_list_get_remove(lib, files):
    ref = lib.add(files / "original.pdf", "March 2025", issuer="Test Bank")
    assert ref["rows"] == len(ROWS) and ref["snapshot"]["rows"][2]["amount"] == "1450.00"
    assert [r["label"] for r in lib.list()] == ["March 2025"]
    assert (lib.root / "files" / f"{ref['id']}.pdf").exists()
    assert lib.get("../etc/passwd") is None
    assert lib.remove(ref["id"]) and lib.list() == []
    assert not (lib.root / "files" / f"{ref['id']}.pdf").exists()


def test_library_rejects_duplicates_blank_labels_and_bad_types(lib, files):
    lib.add(files / "original.pdf", "A")
    with pytest.raises(ValueError, match="already stored"):
        lib.add(files / "original.pdf", "B")
    with pytest.raises(ValueError, match="label"):
        lib.add(files / "forged.pdf", "  ")
    bad = files / "x.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="unsupported"):
        lib.add(bad, "C")


def test_library_is_private(lib, files):
    lib.add(files / "original.pdf", "A")
    assert (lib.root.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in (lib.root / "files").iterdir())


# --------------------------------------------------------------- comparison
def test_consistent_forgery_passes_arithmetic_but_is_caught_by_original(lib, files):
    alone = analyze(files / "forged.pdf")
    assert alone["top_severity"] < Severity.MEDIUM, "premise: arithmetic alone cannot see this"

    lib.add(files / "original.pdf", "March 2025")
    r = analyze(files / "forged.pdf", library=lib, against="auto")
    assert r["reference"]["label"] == "March 2025"
    assert r["top_severity"] == Severity.HIGH and "vs-original" in checks(r)
    amount_diffs = [d for d in r["comparison"]["diffs"] if d["field"] == "amount"]
    assert [(d["submitted"], d["original"]) for d in amount_diffs] == [("145.00", "1,450.00")]
    assert any(d["field"] == "balance" for d in r["comparison"]["diffs"])  # downstream balances differ


def test_identical_file_matches_cleanly(lib, files):
    lib.add(files / "original.pdf", "A")
    copy = files / "copy.pdf"
    shutil.copyfile(files / "original.pdf", copy)
    r = analyze(copy, library=lib, against="auto")
    assert r["comparison"]["identical_file"] and r["top_severity"] < Severity.MEDIUM


def test_unrelated_statement_is_not_compared(lib, files):
    lib.add(files / "original.pdf", "A")
    r = analyze(files / "other.pdf", library=lib, against="auto")
    assert r["reference"] is None and "vs-original" not in checks(r)


def test_explicit_unknown_reference_errors(lib, files):
    with pytest.raises(ValueError):
        analyze(files / "forged.pdf", library=lib, against="deadbeef")


def test_layout_drift_against_original(lib, files):
    lib.add(files / "original.pdf", "A")
    build_pdf(files / "moved.pdf", shift={3: 8}, font_override={1: "Courier"})
    r = analyze(files / "moved.pdf", library=lib, against="auto")
    assert not r["comparison"]["diffs"]  # same content...
    assert "layout-vs-original" in checks(r)  # ...but moved/different-font lines are flagged


def test_added_and_missing_rows(lib, files):
    lib.add(files / "original.pdf", "A")
    build_pdf(files / "fewer.pdf", rows=ROWS[:4] + ROWS[5:])
    r = analyze(files / "fewer.pdf", library=lib, against="auto")
    kinds = {d["kind"] for d in r["comparison"]["diffs"]}
    assert "missing" in kinds


# --------------------------------------------------------- corrections/export
def test_export_restores_to_original_values(lib, files):
    lib.add(files / "original.pdf", "March 2025")
    r = analyze(files / "forged.pdf", library=lib, against="auto")
    ex = build_export(r)
    rent = next(x for x in ex["rows"] if x["description"] == "Rent March")
    assert rent["status"] == "RESTORED" and rent["amount"] == "1450.00"
    assert rent["submitted"].startswith("amount: 145.00")
    orig = {x["desc"]: x for x in analyze(files / "original.pdf", library=lib,
                                                  against="auto")["reference"]["snapshot"]["rows"]}
    for row in ex["rows"]:
        assert row["balance"] == f"{Decimal(orig[row['description']]['balance']):.2f}"
    assert ex["counts"]["UNCHANGED"] >= 2


def test_no_source_means_no_recalculation(files):
    """A statement whose balance was edited, with no stored original: nothing is 'fixed'."""
    build_pdf(files / "t.pdf", balance_override={2: Decimal("2000.00")})
    r = analyze(files / "t.pdf")
    ex = build_export(r)
    row = next(x for x in ex["rows"] if x["description"] == "Rent March")
    assert row["status"] == "UNVERIFIED" and row["balance"] == "2000.00"  # exactly as submitted
    assert "No verified source" in row["note"]
    assert not any(x["status"] in ("RESTORED", "OCR-CORRECTED") for x in ex["rows"])


def test_confirmed_ocr_fix_applies_only_when_accepted():
    text = ("Opening balance 2,263.60\n12/03/2025 Energy bill 212.16 2,051.45\n"
            "15/03/2025 Cafe Nero 18.50 2,032.95\n")
    st = parse_statement(text)
    check_arithmetic(st, ocr=True)
    assert st.ocr_suggestions and st.ocr_suggestions[0]["suggested"] == "212.15"
    fake = {"statement": st, "comparison": None, "reference": None, "file": "x.jpg",
            "ocr_suggestions": st.ocr_suggestions}
    none = build_export(fake)
    assert none["rows"][0]["amount"] == "212.16" and none["rows"][0]["status"] == "UNVERIFIED"
    accepted = build_export(fake, [st.ocr_suggestions[0]["id"]])
    assert accepted["rows"][0]["amount"] == "212.15" and accepted["rows"][0]["status"] == "OCR-CORRECTED"
    assert build_export(fake, ["99:amount"])["rows"][0]["amount"] == "212.16"  # unknown ids ignored


def test_csv_neutralises_formula_injection():
    st = parse_statement("Opening balance 10.00\n01/01/2025 =HYPERLINK(\"x\") 5.00 15.00\n")
    fake = {"statement": st, "comparison": None, "reference": None, "file": "x.txt",
            "verdict": "v", "ocr_suggestions": []}
    csv_bytes = to_csv(fake, build_export(fake)).decode()
    assert "'=HYPERLINK" in csv_bytes and ",=HYPERLINK" not in csv_bytes


def test_pdf_report_is_marked_as_not_a_statement(lib, files):
    lib.add(files / "original.pdf", "March 2025")
    r = analyze(files / "forged.pdf", library=lib, against="auto")
    pdf = to_pdf(r, build_export(r))
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    text = " ".join(p.extract_text() for p in reader.pages)
    assert "NOT a bank statement" in text and "RESTORED" in text
    assert "Differences from the stored original" in text


# ---------------------------------------------------------------------- CLI
def test_cli_library_and_export(files, tmp_path, capsys):
    libdir = str(tmp_path / "cli-lib")
    assert main(["library", "--library", libdir, "add", str(files / "original.pdf"),
                 "--label", "Mar"]) == 0
    assert main(["library", "--library", libdir, "list"]) == 0
    assert "Mar" in capsys.readouterr().out
    out = tmp_path / "out"
    code = main([str(files / "forged.pdf"), "--against", "auto", "--library", libdir,
                 "--export-dir", str(out)])
    assert code == 2
    assert (out / "forged-corrected.csv").exists() and (out / "forged-reconciliation.pdf").exists()
    csv_text = (out / "forged-corrected.csv").read_text()
    assert "RESTORED" in csv_text and "1450.00" in csv_text
    rid = Library(libdir).list()[0]["id"]
    assert main(["library", "--library", libdir, "remove", rid]) == 0
    assert main(["library", "--library", libdir, "remove", rid]) == 1


# ---------------------------------------------------------------------- web
@pytest.fixture()
def client(tmp_path):
    app = create_app(library_root=tmp_path / "weblib")
    c = app.test_client()
    c.csrf = app.csrf_token
    return c


def _form(client, url, fields, **files_):
    data = dict(fields, csrf=client.csrf)
    data.update({k: (io.BytesIO(v[1]), v[0]) for k, v in files_.items()})
    return client.post(url, data=data, content_type="multipart/form-data")


def test_web_menu_and_pages(client):
    for path, text in (("/", b"Check a statement"), ("/library", b"Reference library"), ("/about", b"About")):
        r = client.get(path)
        assert r.status_code == 200 and text in r.data
        assert b"Check statement" in r.data and b"Reference library" in r.data and b"About" in r.data


def test_web_csrf_enforced(client, files):
    r = client.post("/library/add", data={"label": "x", "file": (io.BytesIO(b"x"), "a.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 403


def test_web_full_flow_library_compare_restore_export(client, files):
    add = _form(client, "/library/add", {"label": "March 2025", "issuer": "Test Bank"},
                file=("original.pdf", (files / "original.pdf").read_bytes()))
    assert add.status_code == 200 and b"Stored" in add.data
    assert b"March 2025" in client.get("/library").data
    assert b"March 2025" in client.get("/").data  # appears in the compare dropdown

    res = _form(client, "/analyze", {"against": "auto"},
                file=("forged.pdf", (files / "forged.pdf").read_bytes()))
    html = res.data.decode()
    assert res.status_code == 200
    assert "STRONG INDICATORS" in html and "Differences from the original" in html
    assert "restored to the value in your stored" in html
    token = re.search(r'action="/export/([^"]+)"', html)[1]

    csv_r = _form(client, f"/export/{token}", {"fmt": "csv"})
    assert csv_r.status_code == 200 and csv_r.mimetype == "text/csv"
    assert b"RESTORED" in csv_r.data and b"1450.00" in csv_r.data
    pdf_r = _form(client, f"/export/{token}", {"fmt": "pdf"})
    assert pdf_r.status_code == 200 and pdf_r.data.startswith(b"%PDF")
    assert _form(client, "/export/not-a-token", {"fmt": "pdf"}).status_code == 410


def test_web_no_source_page_explains_no_recalculation(client, files):
    build_pdf(files / "t.pdf", balance_override={2: Decimal("2000.00")})
    res = _form(client, "/analyze", {"against": "none"}, file=("t.pdf", (files / "t.pdf").read_bytes()))
    html = res.data.decode()
    assert "no verified source" in html and "not recalculated" in html.lower()


def test_web_delete_and_validation(client, files):
    _form(client, "/library/add", {"label": "A"}, file=("o.pdf", (files / "original.pdf").read_bytes()))
    rid = re.search(r"/library/([0-9a-f]{16})/delete", client.get("/library").data.decode())[1]
    dup = _form(client, "/library/add", {"label": "B"}, file=("o.pdf", (files / "original.pdf").read_bytes()))
    assert dup.status_code == 400 and b"already stored" in dup.data
    assert _form(client, "/library/add", {"label": ""},
                 file=("f.pdf", (files / "forged.pdf").read_bytes())).status_code == 400
    assert b"Removed" in _form(client, f"/library/{rid}/delete", {}).data
    assert b"No originals stored yet" in client.get("/library").data
