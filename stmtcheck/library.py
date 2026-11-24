"""Reference library: known-genuine original statements kept for later comparison.

Layout on disk (default ~/.stmtcheck, override with STMTCHECK_HOME or the root argument):
    library.db      SQLite index: label, issuer, notes, hash and a parsed snapshot of each original
    files/<id>.ext  the stored original (named by random id, never by the uploaded filename)

Statements are sensitive. The directory is created owner-only (0700), files 0600.
Only add documents you have good reason to believe are genuine (ideally obtained directly from
the issuer): the library is the source of truth that later submissions are judged against.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .analyze import SUPPORTED, analyze, sha256_file
from .compare import make_snapshot, _match
from .models import Severity

MAX_ENTRIES = 500


class Library:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("STMTCHECK_HOME") or Path.home() / ".stmtcheck")
        self.files = self.root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(self.files, 0o700)
        except OSError:
            pass
        self._db = self.root / "library.db"
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS refs (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, issuer TEXT, notes TEXT,
                filename TEXT, stored_name TEXT NOT NULL, sha256 TEXT NOT NULL,
                added_at TEXT NOT NULL, self_check TEXT, rows INTEGER, snapshot TEXT NOT NULL)""")
        try:
            os.chmod(self._db, 0o600)
        except OSError:
            pass

    def _connect(self):
        c = sqlite3.connect(self._db)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ write
    def add(self, path, label: str, issuer: str = "", notes: str = "",
            original_name: str = "") -> dict:
        path = Path(path)
        label = (label or "").strip()
        if not label:
            raise ValueError("a label is required")
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError(f"unsupported file type {path.suffix!r}")
        if len(self.list()) >= MAX_ENTRIES:
            raise ValueError("library is full")
        sha = sha256_file(path)
        existing = self._one("SELECT id, label FROM refs WHERE sha256=?", (sha,))
        if existing:
            raise ValueError(f"this exact file is already stored as {existing['label']!r}")

        result = analyze(path)  # also tells us whether the "original" is itself consistent
        snap = make_snapshot(result)
        rid = secrets.token_hex(8)
        stored = f"{rid}{path.suffix.lower()}"
        dest = self.files / stored
        shutil.copyfile(path, dest)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        added = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with self._connect() as c:
            c.execute("INSERT INTO refs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, label, issuer.strip(), notes.strip(), original_name or path.name, stored,
                       sha, added, result["top_severity"].name, len(snap["rows"]), json.dumps(snap)))
        return self.get(rid)

    def remove(self, rid: str) -> bool:
        row = self._one("SELECT stored_name FROM refs WHERE id=?", (rid,))
        if not row:
            return False
        with self._connect() as c:
            c.execute("DELETE FROM refs WHERE id=?", (rid,))
        (self.files / row["stored_name"]).unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------------- read
    def _one(self, sql, args=()):
        with self._connect() as c:
            return c.execute(sql, args).fetchone()

    def list(self) -> list:
        with self._connect() as c:
            rows = c.execute("SELECT id,label,issuer,notes,filename,sha256,added_at,self_check,rows "
                             "FROM refs ORDER BY added_at DESC, label").fetchall()
        return [dict(r) for r in rows]

    def get(self, rid: str) -> Optional[dict]:
        if not rid or not all(ch in "0123456789abcdef" for ch in rid):
            return None
        row = self._one("SELECT * FROM refs WHERE id=?", (rid,))
        if not row:
            return None
        d = dict(row)
        d["snapshot"] = json.loads(d["snapshot"])
        return d

    def best_match(self, snapshot: dict, sha: str = "") -> Optional[dict]:
        """The stored original that best matches a candidate: identical file first, then the
        highest row overlap (at least 50%)."""
        hit = self._one("SELECT id FROM refs WHERE sha256=?", (sha,)) if sha else None
        if hit:
            return self.get(hit["id"])
        best, best_score = None, 0.5
        with self._connect() as c:
            rows = c.execute("SELECT id, snapshot FROM refs").fetchall()
        for r in rows:
            ref_rows = json.loads(r["snapshot"])["rows"]
            if not ref_rows or not snapshot["rows"]:
                continue
            score = len(_match(snapshot["rows"], ref_rows)) / max(len(snapshot["rows"]), len(ref_rows))
            if score >= best_score:
                best, best_score = r["id"], score
        return self.get(best) if best else None

    def stored_path(self, rid: str) -> Optional[Path]:
        ref = self._one("SELECT stored_name FROM refs WHERE id=?", (rid,))
        return self.files / ref["stored_name"] if ref else None
