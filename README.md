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

### Standalone executable
```
pip install -e '.[dev]' pyinstaller
python build_exe.py            # -> dist/stmtcheck  (dist/stmtcheck.exe on Windows)
```
- **Double-click / run with no arguments:** starts the web app and opens it in your browser (closing the
  window stops it). With arguments it is the same CLI: `stmtcheck statement.pdf`, `stmtcheck library ...`.
- PyInstaller cannot cross-compile: build on Windows for a `.exe`, on macOS for a Mac binary, on Linux
  for Linux. `.github/workflows/build.yml` builds all three (run it from the Actions tab or push a `v*` tag).
- **OCR needs Tesseract, which is not inside the executable.** Install it (Windows installer, `brew install
  tesseract`, `apt install tesseract-ocr`), or ship a portable copy in a `tesseract/` folder beside the
  executable (containing `tesseract[.exe]` and `tessdata/`). Without it everything else works and scans
  report that OCR is unavailable.
- The binary is unsigned, so Windows SmartScreen and macOS Gatekeeper will warn on first run.

### Phone / tablet
The web app is responsive (checked at iPhone width: no sideways scrolling, full-width buttons) and
installable to the home screen (web manifest + icons). To use it from a phone, run the app on a computer
and start it for your network:
```
stmtcheck serve --lan            # prints http://<computer-ip>:8000 and a one-off access code
```
Open that address on the phone (same Wi-Fi), enter the code, then choose a photo or PDF; the phone's camera
is offered for image uploads. Everything is behind the access code (HttpOnly, SameSite=Strict cookie; five
wrong attempts lock that address out for 60 s; API callers send `X-Access-Code`). `--lan` is **plain HTTP**:
use it on a network you trust. For anything beyond that, put it behind an HTTPS reverse proxy or VPN. The
server refuses to listen beyond this computer without a code.
There is **no native iOS/Android app**: those need Xcode / the Android SDK, and the analysis engine
(Python, Tesseract) runs on the computer, not the phone.

### Android app (self-contained)
The Android app runs everything **on the phone**: the Python engine (via Chaquopy), OCR (Google ML Kit, bundled
model, works offline) and PDF rendering (Android PdfRenderer). There is no server to set up and statements are
never uploaded. Tap the icon and it opens.

- **Install:** run the "Build Android app" workflow (Actions tab; it also runs on pushes to this branch), download
  the `stmtcheck-android-apk` artifact, copy `stmtcheck-android.apk` to the phone, allow "install unknown apps"
  for your file manager or browser, and open it. The APK is debug-signed (installable, not for the Play Store);
  Play Protect may warn. It is ~70 MB (arm64 and x86_64 builds of Python, numpy and Pillow).
- **Use:** pick a PDF or photo (the camera is offered), or share / "Open with" a PDF or image from Files, Drive or
  email straight into the app. The reference library lives in the app's private storage. Exports are saved to
  Downloads. Scanned pages take longer than text PDFs (about 10-20 s per page on an emulator).
- **How it stays private:** the engine listens on 127.0.0.1 on a random port, which other apps on a phone could
  reach, so it requires a session and hands it only to the app's own WebView through a per-launch secret URL.
  Only HTTPS is allowed except for that loopback address. The app backs nothing up (`allowBackup=false`), so
  uninstalling removes the library.
- **CI proves it on Android:** the workflow installs the APK in an emulator and, through the app's own server,
  uploads a tampered text PDF, a clean PDF (no false alarm), a scanned photo, and a flattened scanned PDF, then
  stores an original, compares against it and downloads the PDF report. The emulator is x86_64 Android 11; it is
  not a physical phone.

### Cloud server (optional, for browsers)
The Docker setup in `deploy/` (Caddy gives automatic HTTPS) is for using the web app from any browser. The
Android app does not need it. See `deploy/docker-compose.yml`; `STMTCHECK_ACCESS_CODE` (12+ characters) is
required, and the app refuses to start without it. Untested here with a real Docker host.

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
- The Android app has been tested in an emulator in CI, not on a physical phone. The camera capture, the file
  picker and "Open with" / Share hand-off are not covered by that test (only their server side is). The Docker
  setup has not been run on a real Docker host.
- Analysis results behind the export buttons are held in memory for 20 minutes, then dropped.
- All tests use synthetic, fictional statements. Real bank layouts will need adjustment, and detection
  rates on real forgeries have not been measured.
