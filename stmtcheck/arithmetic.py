"""Arithmetic consistency checks: running balance, totals, ordering, duplicates."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from .models import Finding, ParsedStatement, Severity
from .parser import parse_money

# Characters OCR engines commonly confuse. A break that disappears when one printed figure has
# one character swapped for a lookalike is more likely a misread than an edit.
OCR_CONFUSIONS = {
    "0": "86", "1": "7", "2": "7", "3": "8", "5": "6", "6": "58", "7": "12", "8": "035",
    "9": "4", ".": ",", ",": ".",
}
LOW_OCR_CONF = 85.0


def _money(d: Decimal) -> str:
    return f"{d:,.2f}"


def _variants(token: str):
    """The token itself plus every single-character OCR-confusion variant."""
    yield token
    for i, ch in enumerate(token):
        for alt in OCR_CONFUSIONS.get(ch, ""):
            yield token[:i] + alt + token[i + 1:]


def _val(token: str):
    try:
        return parse_money(token)[0]
    except (InvalidOperation, ValueError):
        return None


def _ocr_explains(prev_text: str, amount_text: str, balance_text: str):
    """(figure index, variant) if swapping one lookalike character in one figure reconciles
    the row (0 = previous balance, 1 = amount, 2 = balance); otherwise None."""
    for which in range(3):
        texts = [prev_text, amount_text, balance_text]
        for variant in _variants(texts[which]):
            texts[which] = variant
            p, a, b = (_val(t) for t in texts)
            if None in (p, a, b):
                continue
            if b - p in (abs(a), -abs(a)):
                return which, variant
    return None


def check_arithmetic(st: ParsedStatement, ocr: bool = False) -> list:
    findings: list = []
    txns = st.transactions
    if not txns:
        findings.append(Finding(
            Severity.INFO, "parse", "No transaction rows could be read from the text layer.",
            evidence="The statement may be image-only or use an unsupported layout."))
        return findings

    gap_lines = sorted(n for n, _ in st.unparsed_lines)
    have_gaps = bool(gap_lines)
    prev = st.opening_balance
    prev_text = f"{prev:.2f}" if prev is not None else ""
    if prev is None:
        findings.append(Finding(
            Severity.INFO, "parse", "No opening balance found; the first row cannot be reconciled."))
    debits = credits = Decimal("0")
    last_break_row = None
    last_line = 0

    for i, t in enumerate(txns):
        loc = f"line {t.line_no}, page {t.page}" if t.page else f"line {t.line_no}"
        skipped = [n for n in gap_lines if last_line < n < t.line_no]
        if skipped and prev is not None:
            findings.append(Finding(
                Severity.LOW, "unreadable-row",
                "A dated line could not be read as a transaction, so the balance chain cannot be "
                "verified across it.", f"line {skipped[0]}",
                "Check this row by eye. " + ("OCR may have dropped a figure." if ocr else
                                              "Its layout is not recognised by the parser.")))
            prev = None
        last_line = t.line_no

        if prev is not None:
            delta = t.balance - prev
            exact = 0  # direction when the balance movement matches the amount exactly
            if delta == t.amount:
                direction = exact = 1
            elif delta == -t.amount:
                direction = exact = -1
            else:
                up, down = prev + t.amount, prev - t.amount
                sev, note = Severity.HIGH, ""
                if last_break_row == i - 1:
                    note = (" Consecutive breaks usually mean a balance figure on the previous "
                            "row was changed, not the amount on this one.")
                st.break_lines.append(t.line_no)
                if ocr:
                    fix = _ocr_explains(prev_text, t.amount_text, t.balance_text)
                    if fix:
                        which, variant = fix
                        if which == 0:
                            target = txns[i - 1].line_no if i else 0  # 0 = opening balance
                            field_, shown = ("balance" if i else "opening"), prev_text
                        else:
                            target = t.line_no
                            field_, shown = (("amount", t.amount_text) if which == 1
                                             else ("balance", t.balance_text))
                        st.ocr_suggestions.append({
                            "id": f"{target}:{field_}", "line_no": target, "field": field_,
                            "submitted": shown, "suggested": variant})
                        sev = Severity.MEDIUM
                        note += (" A single-character OCR confusion would reconcile this row, so "
                                 "it may be a misread rather than an edit.")
                    elif t.conf is not None and t.conf < LOW_OCR_CONF:
                        sev = Severity.MEDIUM
                        note += f" OCR confidence for these figures is low ({t.conf:.0f}%)."
                findings.append(Finding(
                    sev, "running-balance",
                    f"Balance does not follow from the previous balance and the amount.{note}",
                    loc,
                    f"previous balance {_money(prev)}; amount {_money(t.amount)}; "
                    f"expected {_money(up)} (credit) or {_money(down)} (debit); "
                    f"printed {_money(t.balance)}. Row: {t.raw}"))
                last_break_row = i
                direction = t.direction_hint or (1 if delta > 0 else -1)
            if exact and t.direction_hint and exact != t.direction_hint:
                findings.append(Finding(
                    Severity.HIGH if not ocr else Severity.MEDIUM, "direction-marker",
                    "Debit/credit marker on the amount contradicts the balance movement.",
                    loc, t.raw))
            if direction == 1:
                credits += t.amount
            else:
                debits += t.amount
        prev = t.balance
        prev_text = t.balance_text

    # Totals are only meaningful when every row was read and the figures are trustworthy.
    tot_sev = Severity.MEDIUM if (ocr or have_gaps) else Severity.HIGH
    tot_note = (" (rows could not all be read, so this may be a reading error)"
                if (ocr or have_gaps) else "")
    if st.total_debits is not None and st.opening_balance is not None and debits != st.total_debits:
        findings.append(Finding(
            tot_sev, "totals",
            "Sum of debit rows does not match the printed total debits." + tot_note,
            evidence=f"rows sum to {_money(debits)}; statement prints {_money(st.total_debits)}"))
    if st.total_credits is not None and st.opening_balance is not None and credits != st.total_credits:
        findings.append(Finding(
            tot_sev, "totals",
            "Sum of credit rows does not match the printed total credits." + tot_note,
            evidence=f"rows sum to {_money(credits)}; statement prints {_money(st.total_credits)}"))

    if st.closing_balance is not None:
        if txns[-1].balance != st.closing_balance:
            findings.append(Finding(
                tot_sev, "closing-balance",
                "Last running balance differs from the printed closing balance." + tot_note,
                evidence=f"last row {_money(txns[-1].balance)}; closing {_money(st.closing_balance)}"))
        if st.opening_balance is not None and st.total_debits is not None \
                and st.total_credits is not None:
            expected = st.opening_balance + st.total_credits - st.total_debits
            if expected != st.closing_balance:
                findings.append(Finding(
                    tot_sev, "summary-box",
                    "Opening + credits - debits does not equal the closing balance." + tot_note,
                    evidence=f"computes to {_money(expected)}; printed {_money(st.closing_balance)}"))

    # Date order (rows with a year, or month/day when the year is omitted).
    for a, b in zip(txns, txns[1:]):
        if b.date < a.date and a.date[1] and b.date[1]:
            findings.append(Finding(
                Severity.MEDIUM if not ocr else Severity.LOW, "date-order",
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
    return findings
