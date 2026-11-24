"""Corrected-data export and reconciliation report.

Policy: a figure is only changed when there is a verified source for the change:
  * a stored original from the reference library (restore to the original's value), or
  * an OCR single-character misread that the user has explicitly confirmed.
Balances are never "recalculated" to make a document add up: if nothing verified says which
figure is wrong, the row is exported unchanged and marked UNVERIFIED.

The output is derived data and a report. It is deliberately not laid out like a bank statement
and every page of the PDF says so.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal

BANNER = "RECONCILIATION REPORT - derived data, NOT a bank statement. Do not present as an original."
NO_SOURCE = ("No verified source: figure not changed. Obtain a certified copy from the issuing bank, "
             "or add the original to the reference library and re-run.")


def _date(d) -> str:
    y, m, dd = d
    return f"{dd:02d}/{m:02d}/{y}" if y else f"{dd:02d}/{m:02d}"


def _num(v) -> str:
    return f"{Decimal(str(v).replace(',', '')):.2f}"


def build_export(result: dict, accepted_fixes=()) -> dict:
    """Return {"rows": [...], "header": [...], "notes": [...], "counts": {...}}."""
    accepted = set(accepted_fixes or ())
    st = result["statement"]
    comp, ref = result.get("comparison"), result.get("reference")
    rows, header, notes = [], [], []

    if comp and comp["same_statement"] and not comp["identical_file"]:
        snap = ref["snapshot"]
        pair_by_ref = {j: line for line, j in comp["pairs"]}
        diffs_by_ref: dict = {}
        for d in comp["diffs"]:
            if d["kind"] == "changed":
                diffs_by_ref.setdefault(d["ref_index"], []).append(d)
        for j, r in enumerate(snap["rows"]):
            base = {"date": _date(r["date"]), "description": r["desc"], "amount": _num(r["amount"]),
                    "balance": _num(r["balance"]), "submitted": "", "note": ""}
            if j not in pair_by_ref:
                rows.append(dict(base, status="REINSERTED",
                                 note="In the original but absent from the submitted statement."))
            elif j in diffs_by_ref:
                ds = diffs_by_ref[j]
                rows.append(dict(base, status="RESTORED",
                                 submitted="; ".join(f"{d['field']}: {d['submitted']}" for d in ds),
                                 note="Restored to the stored original: " + "; ".join(
                                     f"{d['field']} {d['submitted']} -> {d['original']}" for d in ds)))
            else:
                rows.append(dict(base, status="UNCHANGED"))
        for d in comp["diffs"]:
            if d["kind"] == "added":
                rows.append({"date": "", "description": d["row"], "amount": "", "balance": "",
                             "status": "EXCLUDED", "submitted": d["submitted"],
                             "note": "Not in the stored original; excluded from the restored data."})
            elif d["kind"] == "header":
                header.append(f"{d['row']}: submitted {d['submitted']} -> original {d['original']}")
        notes.append(f"Source of truth: stored original '{ref['label']}' (added {ref['added_at']}).")
    else:
        fixes = {s["id"]: s for s in result.get("ocr_suggestions", [])}
        applied = {fid: fixes[fid] for fid in accepted if fid in fixes}
        breaks = set(st.break_lines)
        for t in st.transactions:
            amount, balance = t.amount_text or str(t.amount), t.balance_text or str(t.balance)
            status, note, submitted = "UNCHANGED", "", ""
            for fid, fx in applied.items():
                if fx["line_no"] == t.line_no:
                    old = amount if fx["field"] == "amount" else balance
                    if fx["field"] == "amount":
                        amount = fx["suggested"]
                    else:
                        balance = fx["suggested"]
                    status = "OCR-CORRECTED"
                    submitted = f"{fx['field']}: {old}"
                    note = f"Confirmed OCR misread: {old} -> {fx['suggested']}"
            if status == "UNCHANGED" and t.line_no in breaks:
                status, note = "UNVERIFIED", NO_SOURCE
            rows.append({"date": _date(t.date), "description": t.description,
                         "amount": _num(amount), "balance": _num(balance),
                         "status": status, "submitted": submitted, "note": note})
        if not (comp and comp["identical_file"]):
            notes.append("No stored original was used, so no figure was restored. "
                         "Only user-confirmed OCR misreads were changed.")
        else:
            notes.append("The file is byte-identical to the stored original.")

    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"rows": rows, "header": header, "notes": notes, "counts": counts}


def to_csv(result: dict, export: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# " + BANNER])
    w.writerow(["# source file", result["file"], "verdict", result["verdict"]])
    for n in export["notes"] + export["header"]:
        w.writerow(["# " + n])
    w.writerow(["date", "description", "amount", "balance", "status", "submitted_value", "note"])
    for r in export["rows"]:
        w.writerow([_csv_safe(r[k]) for k in
                    ("date", "description", "amount", "balance", "status", "submitted", "note")])
    return buf.getvalue().encode("utf-8")


def _csv_safe(v: str) -> str:
    """Neutralise spreadsheet formula injection from text lifted out of an untrusted document."""
    return "'" + v if v[:1] in ("=", "+", "-", "@") and not _looks_numeric(v) else v


def _looks_numeric(v: str) -> bool:
    try:
        Decimal(v.replace(",", ""))
        return True
    except Exception:
        return False


def to_pdf(result: dict, export: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    P = lambda t, st=small: Paragraph(escape(str(t)), st)

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#8a1c1c"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 12 * mm, BANNER)
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, "Findings are indicators, not proof. Confirm important "
                          "documents with the issuing bank.")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=20 * mm, bottomMargin=16 * mm, title="Reconciliation report",
                            author="stmtcheck")
    el = [Paragraph("Statement reconciliation report", ss["Title"])]
    ref = result.get("reference")
    meta = [["File", result["file"].split("/")[-1]],
            ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")],
            ["Verdict", result["verdict"]],
            ["Rows checked", str(result["rows_checked"])],
            ["Compared with", f"{ref['label']} (added {ref['added_at']})" if ref else "no stored original"],
            ["Summary", ", ".join(f"{v} {k.lower()}" for k, v in sorted(export["counts"].items())) or "-"]]
    t = Table([[P(a, body), P(b, body)] for a, b in meta], colWidths=[30 * mm, 150 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    el += [t, Spacer(1, 4)]
    for n in export["notes"]:
        el.append(P(n, body))

    el.append(Paragraph("Findings", h2))
    if result["findings"]:
        data = [[P("Severity", small), P("Check", small), P("Where", small), P("Detail", small)]]
        for f in result["findings"]:
            detail = f.message + (f" {f.evidence}" if f.evidence else "")
            data.append([P(f.severity.name), P(f.check), P(f.location), P(detail)])
        ft = Table(data, colWidths=[18 * mm, 28 * mm, 24 * mm, 110 * mm], repeatRows=1)
        ft.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9e9e4")),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
        el.append(ft)
    else:
        el.append(P("No findings.", body))

    comp = result.get("comparison")
    if comp and comp["diffs"]:
        el.append(Paragraph("Differences from the stored original", h2))
        data = [[P(x) for x in ("Kind", "Field", "Row", "Submitted", "Original")]]
        for d in comp["diffs"]:
            data.append([P(d["kind"]), P(d["field"]), P(d["row"]), P(d["submitted"]), P(d["original"])])
        dt = Table(data, colWidths=[17 * mm, 30 * mm, 58 * mm, 37 * mm, 37 * mm], repeatRows=1)
        dt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9e9e4")),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
        el.append(dt)

    el.append(Paragraph("Transactions after verified corrections", h2))
    shade = {"RESTORED": "#fff4d6", "REINSERTED": "#fff4d6", "OCR-CORRECTED": "#e6f0ff",
             "UNVERIFIED": "#ffe0e0", "EXCLUDED": "#eeeeee"}
    data = [[P(x) for x in ("Date", "Description", "Amount", "Balance", "Status", "Note")]]
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9e9e4")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]
    for i, r in enumerate(export["rows"], 1):
        data.append([P(r["date"]), P(r["description"]), P(r["amount"]), P(r["balance"]),
                     P(r["status"]), P(r["note"])])
        if r["status"] in shade:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(shade[r["status"]])))
    tt = Table(data, colWidths=[20 * mm, 42 * mm, 20 * mm, 22 * mm, 24 * mm, 52 * mm], repeatRows=1)
    tt.setStyle(TableStyle(style))
    el.append(tt)
    el.append(Spacer(1, 6))
    el.append(P("UNVERIFIED rows are exported exactly as submitted. Balances are never recalculated "
                "to force a statement to add up, because that cannot show which figure is wrong.", body))
    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
