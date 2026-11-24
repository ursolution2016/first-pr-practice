"""Arithmetic consistency checks: running balance, totals, ordering, duplicates."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .models import Finding, ParsedStatement, Severity


def _money(d: Decimal) -> str:
    return f"{d:,.2f}"


def check_arithmetic(st: ParsedStatement) -> list:
    findings: list = []
    txns = st.transactions
    if not txns:
        findings.append(Finding(
            Severity.INFO, "parse", "No transaction rows could be read from the text layer.",
            evidence="The statement may be image-only or use an unsupported layout."))
        return findings

    prev = st.opening_balance
    if prev is None:
        findings.append(Finding(
            Severity.INFO, "parse", "No opening balance found; the first row cannot be reconciled."))
        prev = None
    debits = credits = Decimal("0")
    last_break_row = None

    for i, t in enumerate(txns):
        loc = f"line {t.line_no}" + (f", page {t.page}" if t.page else "")
        if prev is not None:
            delta = t.balance - prev
            if delta == t.amount:
                direction = 1
            elif delta == -t.amount:
                direction = -1
            else:
                direction = 0
                up, down = prev + t.amount, prev - t.amount
                sev = Severity.HIGH
                note = ""
                if last_break_row == i - 1:
                    note = (" Consecutive breaks usually mean a balance figure on the previous "
                            "row was changed, not the amount on this one.")
                findings.append(Finding(
                    sev, "running-balance",
                    f"Balance does not follow from the previous balance and the amount.{note}",
                    loc,
                    f"previous balance {_money(prev)}; amount {_money(t.amount)}; "
                    f"expected {_money(up)} (credit) or {_money(down)} (debit); "
                    f"printed {_money(t.balance)}. Row: {t.raw}"))
                last_break_row = i
            if direction and t.direction_hint and direction != t.direction_hint:
                findings.append(Finding(
                    Severity.HIGH, "direction-marker",
                    "Debit/credit marker on the amount contradicts the balance movement.",
                    loc, t.raw))
            if direction == 1:
                credits += t.amount
            elif direction == -1:
                debits += t.amount
        prev = t.balance

    if st.total_debits is not None and prev is not None and st.opening_balance is not None:
        if debits != st.total_debits:
            findings.append(Finding(
                Severity.HIGH, "totals",
                "Sum of debit rows does not match the printed total debits.",
                evidence=f"rows sum to {_money(debits)}; statement prints {_money(st.total_debits)}"))
    if st.total_credits is not None and st.opening_balance is not None:
        if credits != st.total_credits:
            findings.append(Finding(
                Severity.HIGH, "totals",
                "Sum of credit rows does not match the printed total credits.",
                evidence=f"rows sum to {_money(credits)}; statement prints {_money(st.total_credits)}"))

    if st.closing_balance is not None:
        if txns[-1].balance != st.closing_balance:
            findings.append(Finding(
                Severity.HIGH, "closing-balance",
                "Last running balance differs from the printed closing balance.",
                evidence=f"last row {_money(txns[-1].balance)}; closing {_money(st.closing_balance)}"))
        if st.opening_balance is not None and st.total_debits is not None \
                and st.total_credits is not None:
            expected = st.opening_balance + st.total_credits - st.total_debits
            if expected != st.closing_balance:
                findings.append(Finding(
                    Severity.HIGH, "summary-box",
                    "Opening + credits - debits does not equal the closing balance.",
                    evidence=f"computes to {_money(expected)}; printed {_money(st.closing_balance)}"))

    # Date order (rows with a year, or month/day when the year is omitted).
    for a, b in zip(txns, txns[1:]):
        if b.date < a.date and a.date[1] and b.date[1]:
            findings.append(Finding(
                Severity.MEDIUM, "date-order",
                "Transaction dates run backwards; statements are normally chronological.",
                f"line {b.line_no}", f"{a.raw}  ->  {b.raw}"))

    # Identical rows (same date, description, amount, balance) cannot occur legitimately.
    seen = Counter((t.date, t.description, t.amount, t.balance) for t in txns)
    for key, n in seen.items():
        if n > 1:
            findings.append(Finding(
                Severity.MEDIUM, "duplicate-row",
                f"Row appears {n} times with identical date, description, amount and balance.",
                evidence=f"{key[1]} {_money(key[2])}"))

    if st.unparsed_lines:
        findings.append(Finding(
            Severity.INFO, "parse",
            f"{len(st.unparsed_lines)} dated line(s) lacked an amount and balance and were skipped.",
            evidence="; ".join(f"line {n}" for n, _ in st.unparsed_lines[:10])))
    return findings
