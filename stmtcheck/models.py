"""Shared data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Optional


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class Transaction:
    line_no: int
    page: Optional[int]
    raw: str
    date: tuple  # (year, month, day); year is 0 when the statement omits it
    description: str
    amount: Decimal  # always positive as printed
    balance: Decimal  # signed running balance
    direction_hint: int = 0  # +1 credit, -1 debit, 0 unknown (from CR/DR/minus)
    amount_text: str = ""
    balance_text: str = ""
    conf: Optional[float] = None  # OCR confidence (0-100) of the figures, when read by OCR


@dataclass
class ParsedStatement:
    transactions: list = field(default_factory=list)
    opening_balance: Optional[Decimal] = None
    closing_balance: Optional[Decimal] = None
    total_debits: Optional[Decimal] = None
    total_credits: Optional[Decimal] = None
    unparsed_lines: list = field(default_factory=list)  # (line_no, text)


@dataclass
class Finding:
    severity: Severity
    check: str
    message: str
    location: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.name,
            "check": self.check,
            "message": self.message,
            "location": self.location,
            "evidence": self.evidence,
        }
