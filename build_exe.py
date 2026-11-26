"""Build a single-file executable for the OS you run this on.

    pip install -e '.[dev]' pyinstaller
    python build_exe.py          # -> dist/stmtcheck  (dist/stmtcheck.exe on Windows)

PyInstaller does not cross-compile: build on Windows for a .exe, on macOS for a Mac app binary.
(The GitHub Actions workflow in .github/workflows/build.yml does all three.)
"""
import sys
import tempfile

import PyInstaller.__main__

with tempfile.TemporaryDirectory() as work:
    PyInstaller.__main__.run([
        "stmtcheck_app.py", "--noconfirm", "--clean", "--onefile", "--name", "stmtcheck",
        "--distpath", "dist", "--workpath", work, "--specpath", work,
        "--collect-all", "pypdfium2", "--collect-submodules", "pdfminer", "--collect-submodules", "pytesseract",
        "--hidden-import", "stmtcheck.web", "--hidden-import", "stmtcheck.library",
    ])
print("Built dist/stmtcheck" + (".exe" if sys.platform == "win32" else ""))
