# first-pr-practice

A private sandbox for practicing the pull request workflow.


## stmtcheck: bank statement tamper screening

Screens a statement (PDF, scan/photo, or `.txt` of OCR output) for signs it has been altered.

```
pip install -e '.[dev]'            # OCR also needs the Tesseract binary: apt install tesseract-ocr
python -m stmtcheck statement.pdf  # report; add --json for machine-readable output
python -m stmtcheck scan.jpg --ela-dir out/            # also write ELA heatmaps for human review
python -m stmtcheck learn g1.pdf g2.pdf --name "My Bank" -o bank.json
python -m stmtcheck statement.pdf --profile bank.json  # compare against known-genuine samples
python -m stmtcheck library add original.pdf --label "Acme, March 2025"   # store a genuine original
python -m stmtcheck statement.pdf --against auto --export-dir out/       # compare + export corrections
python -m stmtcheck serve                              # local web UI on http://127.0.0.1:8000
pytest
```

Exit code: `0` nothing at medium or above, `1` suspicious, `2` strong indicators, `3` bad input.

### What it checks
| Check | Applies to | Catches |
|---|---|---|
| Running balance | all | A changed amount or balance: each balance must equal the previous balance +/- the amount. One edited balance breaks two consecutive rows. |
| Totals, summary, closing balance | all | Printed totals versus the rows. |
| Debit/credit markers, date order, duplicates | all | Contradictory markers, reordered or pasted rows. |
| Font and size per column | text-layer PDF | A retyped figure or description in a different font, even when the arithmetic is consistent. |
| Column alignment | PDF and OCR | Retyped balances sitting off the column edge (fitted line, so a skewed scan still passes). |
| PDF edit history, producer, dates, XMP | PDF | Incremental updates, general-purpose editors, modified-after-created. |
| Bank profile | text-layer PDF | Producer, fonts, page size or boilerplate lines that differ from genuine samples. |
| Background noise | scans/images | White-out or digital fills (unnaturally smooth) and pasted patches (much noisier). |
| EXIF | images | Image/document editors named in metadata, capture/modify time mismatch. |
| No text layer | PDF | Flattened images, which hide editing. These are OCR'd and checked as scans. |

### Reference library and comparison
Store known-genuine originals (menu: **Reference library**, or `stmtcheck library add`). A submitted
statement is then compared with the best-matching original (`--against auto`, or pick one):
- **Content:** every changed, added or missing transaction, and any difference in opening/closing
  balance and totals. This is what catches a *consistent* forgery, where someone edits a figure and
  recomputes every balance and total so the arithmetic still adds up.
- **Layout:** each printed line is matched to the same line in the original and compared for
  position and font; producer, creator and page size are compared too (text-layer PDFs).
- Identical files are recognised by hash. Unrelated statements are not compared.

The library lives in `~/.stmtcheck` (override with `--library` or `STMTCHECK_HOME`), owner-only.
Only add documents you know are genuine: everything else is judged against them. A stored original
that itself fails the checks is flagged when added.

### Corrections and export
After a check you can export **corrected data (CSV)** and a **reconciliation report (PDF)**.
Figures are only changed when there is a verified source:
1. **A stored original:** every differing figure is restored to the original's value, missing rows are
   reinserted, extra rows are excluded, and each change is marked (`RESTORED`, `REINSERTED`, `EXCLUDED`).
2. **An OCR misread you confirm:** where a single-character confusion would reconcile a row, the
   suggestion is listed with a checkbox (CLI: `--accept-ocr-fix ID`). Nothing is applied unless ticked.
3. **Otherwise nothing is changed.** Rows that do not reconcile are exported exactly as submitted and
   marked `UNVERIFIED`.

Balances are deliberately **never recalculated** to make a statement add up. If nothing verified says
which figure is wrong, recalculating only hides the problem, and it would turn a tampered document into
one that passes every check. The outputs are derived data, are not laid out like a bank statement, and
every PDF page says "NOT a bank statement". CSV cells are neutralised against spreadsheet formula
injection, because their text comes from an untrusted document.

### OCR handling
Scans are read with Tesseract; amount and balance figures get a second digits-only pass. OCR misreads
digits, so arithmetic breaks in scanned documents are downgraded to MEDIUM when a single-character
confusion (5/6, 0/8, `.`/`,` ...) would reconcile the row or confidence is low, and are labelled
"verify against the image". Rows that cannot be read are reported and the balance chain is not
compared across them. Always confirm OCR-based findings by eye.

### Bank profiles
`stmtcheck learn` builds a profile from two or more statements you know are genuine. None are
bundled: they cannot be verified here, and a wrong profile would mislead. Re-learn when a bank
changes its statement system.

### Web UI
`stmtcheck serve` runs a local app with a menu: **Check statement**, **Reference library** (add, list and
remove originals) and **About**. Forms carry a CSRF token. Check uploads are never stored. Uploads go to a private temp directory, are analysed,
and are deleted immediately; nothing is stored or logged. The client filename is never used on disk,
types are allow-listed and uploads capped at 25 MB. It binds to 127.0.0.1. Add authentication and TLS
before exposing it to anyone else, because statements are sensitive.

### Limits (read these)
- Findings are indicators, not proof. A clean result does not show a document is genuine. Someone who
  regenerates the whole document with consistent fonts and correct arithmetic can pass the arithmetic,
  layout and metadata checks. Confirm important statements with the issuing bank.
- **ELA is not automated.** I tested error-level analysis on synthetic edits and it did not separate
  edited regions from clean scans reliably, so it only produces a heatmap for a person to inspect.
  The noise check did detect white-out in testing, but it was tested on synthetic scans only.
- Row parsing is heuristic (date, description, amount, balance), day-first dates. Unusual layouts are
  reported as unreadable rather than guessed.
- Copy-move (clone) detection is not implemented.
- Comparison with an original is only as good as the original: a forged "original" in the library will
  vouch for a forgery. Obtain originals directly from the issuer.
- Analysis results behind the export buttons are held in memory for 20 minutes, then dropped.
- All tests use synthetic, fictional statements. Real bank layouts will need adjustment, and detection
  rates on real forgeries have not been measured.
