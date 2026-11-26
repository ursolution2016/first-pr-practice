"""Compare a submitted statement with a stored known-genuine original.

Two comparisons are made:
  * Content: transactions are matched row by row, and every changed, added or missing figure is
    reported, plus the header figures (opening/closing balance, totals).
  * Layout: for text-layer PDFs, each printed line is matched to its counterpart in the original
    (digits masked, so edited numbers still match) and compared for position and font.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .models import Finding, Severity

LAYOUT_TOL = 1.5  # points
MAX_FINDINGS = 25


def _mask(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text)).strip().lower()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def make_snapshot(result: dict) -> dict:
    """Compact, JSON-safe description of an analysed statement."""
    st, lines = result["statement"], result["lines"]
    rows = [{"line": t.line_no, "date": list(t.date), "desc": t.description,
             "amount": str(t.amount), "balance": str(t.balance)} for t in st.transactions]
    layout = []
    if lines is not None and not result["by_ocr"]:
        for ln in lines:
            words = ln["words"]
            if not words:
                continue
            layout.append({
                "key": _mask(ln["text"]), "page": ln["page"],
                "x0": round(words[0]["x0"], 1), "x1": round(words[-1]["x1"], 1),
                "top": round(words[0]["top"], 1),
                "fonts": sorted({f for w in words for f, _ in w.get("fonts", ())}),
            })
    header = {k: (str(getattr(st, k)) if getattr(st, k) is not None else None)
              for k in ("opening_balance", "closing_balance", "total_debits", "total_credits")}
    return {"rows": rows, "header": header, "layout": layout, "by_ocr": result["by_ocr"],
            "meta": result.get("meta", {}), "page_sizes": [list(s) for s in result.get("page_sizes", [])]}


def _match(crow: list, rrow: list) -> list:
    """Pair candidate rows with reference rows: (date,desc), then (date,amount), then (desc,amount)."""
    pairs, used_c, used_r = [], set(), set()
    keyers = (
        lambda r: (tuple(r["date"]), _norm(r["desc"])),
        lambda r: (tuple(r["date"]), Decimal(r["amount"])),
        lambda r: (_norm(r["desc"]), Decimal(r["amount"])),
    )
    for keyer in keyers:
        index: dict = {}
        for j, r in enumerate(rrow):
            if j not in used_r:
                index.setdefault(keyer(r), []).append(j)
        for i, c in enumerate(crow):
            if i in used_c:
                continue
            bucket = index.get(keyer(c))
            if bucket:
                j = bucket.pop(0)
                pairs.append((i, j))
                used_c.add(i)
                used_r.add(j)
    return sorted(pairs)


def _date_str(d) -> str:
    y, m, dd = d
    return f"{dd:02d}/{m:02d}/{y}" if y else f"{dd:02d}/{m:02d}"


def _m(v) -> str:
    return f"{Decimal(v):,.2f}"


def compare(cand: dict, ref: dict, cand_sha: str = "", ref_sha: str = "") -> dict:
    out = {"identical_file": bool(cand_sha) and cand_sha == ref_sha, "same_statement": False,
           "matched": 0, "overlap": 0.0, "diffs": [], "layout": [], "pairs": [], "findings": []}
    f = out["findings"]
    if out["identical_file"]:
        out.update(same_statement=True, overlap=1.0, matched=len(cand["rows"]))
        f.append(Finding(Severity.INFO, "reference",
                         "The file is byte-identical to the stored original."))
        return out

    crow, rrow = cand["rows"], ref["rows"]
    pairs = _match(crow, rrow)
    overlap = len(pairs) / max(len(crow), len(rrow), 1)
    out.update(matched=len(pairs), overlap=overlap, same_statement=overlap >= 0.5,
               pairs=[(crow[i]["line"], j) for i, j in pairs])
    if not out["same_statement"]:
        f.append(Finding(
            Severity.INFO, "reference",
            "The stored original does not appear to be the same statement.",
            evidence=f"only {len(pairs)} of {max(len(crow), len(rrow))} rows match, so no "
                     "row-by-row comparison was made."))
        return out

    diffs = out["diffs"]
    matched_c = {i for i, _ in pairs}
    matched_r = {j for _, j in pairs}
    for i, j in pairs:
        c, r = crow[i], rrow[j]
        where = f"line {c['line']}"
        for name in ("amount", "balance"):
            if Decimal(c[name]) != Decimal(r[name]):
                diffs.append({"kind": "changed", "field": name, "line": c["line"], "ref_index": j,
                              "row": f"{_date_str(r['date'])} {r['desc']}",
                              "submitted": _m(c[name]), "original": _m(r[name])})
        if c["date"] != r["date"]:
            diffs.append({"kind": "changed", "field": "date", "line": c["line"], "ref_index": j,
                          "row": r["desc"], "submitted": _date_str(c["date"]),
                          "original": _date_str(r["date"])})
        if _norm(c["desc"]) != _norm(r["desc"]):
            diffs.append({"kind": "changed", "field": "description", "line": c["line"],
                          "ref_index": j, "row": _date_str(r["date"]),
                          "submitted": c["desc"], "original": r["desc"]})
    for i, c in enumerate(crow):
        if i not in matched_c:
            diffs.append({"kind": "added", "field": "row", "line": c["line"], "ref_index": None,
                          "row": f"{_date_str(c['date'])} {c['desc']}",
                          "submitted": f"{_m(c['amount'])} / balance {_m(c['balance'])}",
                          "original": "(not in the original)"})
    for j, r in enumerate(rrow):
        if j not in matched_r:
            diffs.append({"kind": "missing", "field": "row", "line": None, "ref_index": j,
                          "row": f"{_date_str(r['date'])} {r['desc']}",
                          "submitted": "(absent)",
                          "original": f"{_m(r['amount'])} / balance {_m(r['balance'])}"})
    for key in ("opening_balance", "closing_balance", "total_debits", "total_credits"):
        a, b = cand["header"].get(key), ref["header"].get(key)
        if a is not None and b is not None and Decimal(a) != Decimal(b):
            diffs.append({"kind": "header", "field": key, "line": None, "ref_index": None,
                          "row": key.replace("_", " "), "submitted": _m(a), "original": _m(b)})

    for d in diffs[:MAX_FINDINGS]:
        sev = Severity.MEDIUM if d["field"] in ("date", "description") else Severity.HIGH
        label = {"changed": f"{d['field']} differs from the original",
                 "added": "row is not in the original",
                 "missing": "row from the original is absent",
                 "header": f"{d['row']} differs from the original"}[d["kind"]]
        f.append(Finding(sev, "vs-original", label.capitalize() + ".",
                         f"line {d['line']}" if d["line"] else "",
                         f"{d['row']}: submitted {d['submitted']}; original {d['original']}"))
    if len(diffs) > MAX_FINDINGS:
        f.append(Finding(Severity.HIGH, "vs-original",
                         f"{len(diffs) - MAX_FINDINGS} further difference(s) from the original "
                         "(see the full table)."))

    # Layout comparison (text-layer PDFs on both sides).
    if cand["layout"] and ref["layout"]:
        _compare_layout(cand, ref, out)
    if cand["meta"] and ref["meta"]:
        for k in ("/Producer", "/Creator"):
            if cand["meta"].get(k) != ref["meta"].get(k):
                f.append(Finding(Severity.MEDIUM, "vs-original",
                                 f"{k[1:]} metadata differs from the original.",
                                 evidence=f"submitted {cand['meta'].get(k)!r}; "
                                          f"original {ref['meta'].get(k)!r}"))
    if cand["page_sizes"] and ref["page_sizes"] and cand["page_sizes"] != ref["page_sizes"]:
        f.append(Finding(Severity.MEDIUM, "vs-original", "Page size or count differs from the original.",
                         evidence=f"submitted {cand['page_sizes']}; original {ref['page_sizes']}"))
    return out


def _compare_layout(cand: dict, ref: dict, out: dict) -> None:
    ref_by_key: dict = {}
    for ln in ref["layout"]:
        ref_by_key.setdefault(ln["key"], []).append(ln)
    moved, fonts = [], []
    for ln in cand["layout"]:
        bucket = ref_by_key.get(ln["key"])
        if not bucket:
            continue
        r = bucket.pop(0)
        dx0, dx1, dy = ln["x0"] - r["x0"], ln["x1"] - r["x1"], ln["top"] - r["top"]
        if max(abs(dx0), abs(dx1), abs(dy)) > LAYOUT_TOL or ln["page"] != r["page"]:
            moved.append({"text": ln["key"], "dx0": dx0, "dx1": dx1, "dy": dy, "page": ln["page"]})
        if ln["fonts"] != r["fonts"]:
            fonts.append({"text": ln["key"], "submitted": ln["fonts"], "original": r["fonts"],
                          "page": ln["page"]})
    out["layout"] = moved + fonts
    for m in moved[:10]:
        out["findings"].append(Finding(
            Severity.MEDIUM, "layout-vs-original",
            "A line is positioned differently from the same line in the original.",
            f"page {m['page']}",
            f"'{m['text'][:60]}' moved dx0={m['dx0']:+.1f}, dx1={m['dx1']:+.1f}, dy={m['dy']:+.1f} pt"))
    for m in fonts[:10]:
        out["findings"].append(Finding(
            Severity.MEDIUM, "layout-vs-original",
            "A line uses different fonts from the same line in the original.",
            f"page {m['page']}",
            f"'{m['text'][:60]}': submitted {m['submitted']}; original {m['original']}"))
    extra = len(moved) + len(fonts) - 20
    if extra > 0:
        out["findings"].append(Finding(Severity.MEDIUM, "layout-vs-original",
                                       f"{extra} further layout difference(s) from the original."))
