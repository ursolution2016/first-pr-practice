# first-pr-practice

A private sandbox for practicing the pull request workflow.


## stmtcheck: bank statement tamper screening

Screens a statement (PDF, or `.txt` OCR output) for signs it has been altered.

```
pip install -e '.[dev]'
python -m stmtcheck statement.pdf          # human-readable report
python -m stmtcheck statement.pdf --json   # machine-readable
pytest
```

Exit code: `0` nothing at medium or above, `1` suspicious, `2` strong indicators.

### What it checks
| Check | Catches |
|---|---|
| Running balance | A changed amount or balance: each balance must equal the previous balance +/- the amount. A single edited balance breaks two consecutive rows. |
| Totals and summary | Printed total debits/credits and closing balance versus the rows. |
| Debit/credit markers | `CR`/`DR` or minus signs that contradict the balance movement. |
| Date order, duplicate rows | Pasted or reordered rows. |
| Font and size per column | A figure or description retyped in a different font, even when the arithmetic is fully consistent. |
| Column alignment | Retyped balances sitting a point or two off the column edge. |
| PDF edit history | Incremental updates appended after the original save. |
| Producer, dates, XMP | General-purpose editors in metadata, modification after creation, edit-history records. |
| No text layer | Image-only PDFs, which hide editing and cannot be checked without OCR. |

### Limits (read these)
- Findings are indicators, not proof. A clean result does not show a document is genuine. A forger who
  regenerates the whole document with a consistent font and correct arithmetic will pass the layout and
  arithmetic checks. Confirm important statements with the issuing bank.
- Row parsing is heuristic (date, description, amount, balance). Unusual layouts may be skipped; skipped
  lines are reported.
- Dates are read day-first (AU/UK).
- Scanned documents need OCR first; no OCR engine is bundled.
- Tests use synthetic, fictional statements only.
