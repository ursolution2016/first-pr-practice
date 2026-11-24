"""Tests use synthetic, fictional statements generated on the fly."""
from decimal import Decimal

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from stmtcheck import analyze
from stmtcheck.cli import main
from stmtcheck.models import Severity
from stmtcheck.parser import parse_money, parse_statement

# (date, description, amount, signed delta) - opening balance 1,000.00
ROWS = [
    ("01/03/2025", "Salary ACME PTY LTD", "2,500.00", Decimal("2500.00")),
    ("03/03/2025", "Coles Supermarket", "86.40", Decimal("-86.40")),
    ("05/03/2025", "Rent March", "1,450.00", Decimal("-1450.00")),
    ("09/03/2025", "Transfer from savings", "300.00", Decimal("300.00")),
    ("12/03/2025", "Energy bill", "212.15", Decimal("-212.15")),
    ("15/03/2025", "Cafe Nero", "18.50", Decimal("-18.50")),
]
OPENING = Decimal("1000.00")


def fmt(d):
    return f"{d:,.2f}"


def build_pdf(path, *, rows=ROWS, balance_override=None, font_override=None,
              shift=None, totals_override=None, producer=None, blank=False):
    balance_override = balance_override or {}
    font_override = font_override or {}
    shift = shift or {}
    c = canvas.Canvas(str(path), pagesize=A4)
    if producer:
        c.setProducer(producer)
    if blank:
        c.showPage()
        c.save()
        return
    y = 780
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Opening balance {fmt(OPENING)}")
    y -= 20
    bal = OPENING
    debits = credits = Decimal("0")
    for i, (date, desc, amt, delta) in enumerate(rows):
        bal += delta
        debits += -delta if delta < 0 else 0
        credits += delta if delta > 0 else 0
        printed_bal = balance_override.get(i, bal)
        c.setFont(font_override.get(i, "Helvetica"), 10)
        c.drawString(40, y, date)
        c.setFont("Helvetica", 10)
        c.drawString(110, y, desc)
        c.drawRightString(400, y, amt)
        c.setFont(font_override.get(i, "Helvetica"), 10)
        c.drawRightString(500 + shift.get(i, 0), y, fmt(printed_bal))
        y -= 18
    y -= 10
    c.setFont("Helvetica", 10)
    td, tc = totals_override or (debits, credits)
    c.drawString(40, y, f"Total debits {fmt(td)}")
    y -= 15
    c.drawString(40, y, f"Total credits {fmt(tc)}")
    y -= 15
    c.drawString(40, y, f"Closing balance {fmt(bal)}")
    c.showPage()
    c.save()


def checks(result):
    return [f.check for f in result["findings"]]


def test_parse_money():
    assert parse_money("1,234.50") == (Decimal("1234.50"), 0)
    assert parse_money("(25.00)")[0] == Decimal("-25.00")
    assert parse_money("100.00 DR") == (Decimal("-100.00"), -1)
    assert parse_money("100.00 CR") == (Decimal("100.00"), 1)


def test_parse_text_rows():
    st = parse_statement("Opening balance 10.00\n01/03/2025 Deposit 5.00 15.00\n")
    assert st.opening_balance == Decimal("10.00")
    assert st.transactions[0].balance == Decimal("15.00")


def test_clean_statement_has_no_findings(tmp_path):
    p = tmp_path / "clean.pdf"
    build_pdf(p)
    r = analyze(p)
    assert r["rows_checked"] == len(ROWS)
    assert r["top_severity"] < Severity.MEDIUM, r["findings"]


def test_edited_balance_breaks_two_consecutive_rows(tmp_path):
    p = tmp_path / "t.pdf"
    build_pdf(p, balance_override={2: Decimal("2000.00")})
    r = analyze(p)
    breaks = [f for f in r["findings"] if f.check == "running-balance"]
    assert len(breaks) == 2
    assert any("previous row" in f.message for f in breaks)
    assert r["top_severity"] == Severity.HIGH
    assert main([str(p)]) == 2


def test_edited_amount_is_found(tmp_path):
    rows = list(ROWS)
    rows[1] = ("03/03/2025", "Coles Supermarket", "8.64", rows[1][3])  # amount shown changed
    p = tmp_path / "t.pdf"
    build_pdf(p, rows=rows)
    r = analyze(p)
    assert "running-balance" in checks(r)


def test_totals_mismatch(tmp_path):
    p = tmp_path / "t.pdf"
    build_pdf(p, totals_override=(Decimal("100.00"), Decimal("2800.00")))
    assert "totals" in checks(analyze(p))


def test_consistent_forgery_caught_by_font(tmp_path):
    """Arithmetic is fully consistent, but one retyped row uses a different font."""
    p = tmp_path / "t.pdf"
    build_pdf(p, font_override={3: "Courier"})
    r = analyze(p)
    assert "running-balance" not in checks(r)
    fm = [f for f in r["findings"] if f.check == "font-mismatch"]
    assert fm and all("line 5" in f.location or "line" in f.location for f in fm)
    assert r["top_severity"] >= Severity.MEDIUM


def test_misaligned_balance_flagged(tmp_path):
    p = tmp_path / "t.pdf"
    build_pdf(p, shift={4: 6})
    assert "column-alignment" in checks(analyze(p))


def test_incremental_update_and_editor_producer(tmp_path):
    original = tmp_path / "orig.pdf"
    build_pdf(original)
    assert "edit-history" not in checks(analyze(original))
    edited = tmp_path / "edited.pdf"
    edited.write_bytes(original.read_bytes())
    w = PdfWriter(str(edited), incremental=True)
    w.add_metadata({"/Producer": "Sejda PDF Editor"})
    w.write(str(edited))
    r = analyze(edited)
    assert "edit-history" in checks(r)
    assert "producer" in checks(r)


def test_image_only_pdf_flagged(tmp_path):
    p = tmp_path / "blank.pdf"
    build_pdf(p, blank=True)
    assert "no-text-layer" in checks(analyze(p))


def test_text_input_path(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("Opening balance 100.00\n01/01/2025 Pay 50.00 150.00\n02/01/2025 Shop 20.00 140.00\n"
                 "03/01/2025 Shop 10.00 999.00\nClosing balance 999.00\n")
    r = analyze(p)
    assert [f.check for f in r["findings"] if f.check == "running-balance"]


def test_cli_json(tmp_path, capsys):
    p = tmp_path / "clean.pdf"
    build_pdf(p)
    assert main([str(p), "--json"]) == 0
    assert '"disclaimer"' in capsys.readouterr().out
