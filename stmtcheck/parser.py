"""Turn statement text into transactions, opening/closing balances and totals."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from .models import ParsedStatement, Transaction

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_MONEY = r"\(?-?\$?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?(?:\s?(?:CR|DR))?"
_DATE = (r"(?:\d{4}-\d{2}-\d{2}"
         r"|\d{1,2}/\d{1,2}/\d{2,4}"
         r"|\d{1,2}[ -](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:[ -]\d{2,4})?)")

MONEY_RE = re.compile(_MONEY, re.I)
_ROW_RE = re.compile(rf"^\s*(?P<date>{_DATE})\s+(?P<rest>.+)$", re.I)
_OPEN_RE = re.compile(r"(opening|brought forward|previous|beginning)\s+balance", re.I)
_CLOSE_RE = re.compile(r"(closing|carried forward|ending)\s+balance", re.I)
_TOT_DEBIT_RE = re.compile(r"total\s+(debits?|withdrawals?|money out|payments?)", re.I)
_TOT_CREDIT_RE = re.compile(r"total\s+(credits?|deposits?|money in|receipts?)", re.I)


def parse_money(token: str) -> tuple[Decimal, int]:
    """Return (signed value, direction hint). Hint: +1 CR, -1 DR/negative, 0 none."""
    t = token.strip()
    neg = t.startswith("(") or "-" in t or t.upper().endswith("DR")
    hint = -1 if neg else (1 if t.upper().endswith("CR") else 0)
    digits = re.sub(r"[^\d.]", "", t)
    value = Decimal(digits)
    return (-value if neg else value), hint


def parse_date(text: str) -> tuple:
    t = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)  # day first (AU/UK)
    if m:
        y = int(m[3])
        return (y + 2000 if y < 100 else y), int(m[2]), int(m[1])
    m = re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]+)(?:[ -](\d{2,4}))?", t)
    if m:
        y = int(m[3]) if m[3] else 0
        if 0 < y < 100:
            y += 2000
        return y, _MONTHS.get(m[2][:3].lower(), 0), int(m[1])
    return 0, 0, 0


def _last_money(line: str) -> Optional[Decimal]:
    found = MONEY_RE.findall(line)
    return parse_money(found[-1])[0] if found else None


def parse_statement(text: str, page_of_line: Optional[dict] = None) -> ParsedStatement:
    """Parse text. `page_of_line` optionally maps 1-based line numbers to page numbers."""
    out = ParsedStatement()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        page = (page_of_line or {}).get(line_no)
        if _OPEN_RE.search(line) and out.opening_balance is None:
            out.opening_balance = _last_money(line)
            continue
        if _CLOSE_RE.search(line):
            out.closing_balance = _last_money(line)
            continue
        if _TOT_DEBIT_RE.search(line):
            v = _last_money(line)
            out.total_debits = abs(v) if v is not None else None
            continue
        if _TOT_CREDIT_RE.search(line):
            v = _last_money(line)
            out.total_credits = abs(v) if v is not None else None
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        rest = m["rest"]
        matches = list(MONEY_RE.finditer(rest))
        if len(matches) < 2:
            out.unparsed_lines.append((line_no, line.strip()))
            continue
        amt_m, bal_m = matches[-2], matches[-1]
        amount, hint = parse_money(amt_m[0])
        balance, _ = parse_money(bal_m[0])
        out.transactions.append(Transaction(
            line_no=line_no, page=page, raw=line.strip(),
            date=parse_date(m["date"]),
            description=rest[:amt_m.start()].strip(),
            amount=abs(amount), balance=balance, direction_hint=hint,
            amount_text=amt_m[0].strip(), balance_text=bal_m[0].strip(),
        ))
    return out
