"""Per-bank profiles learned from known-genuine statements.

A profile records what genuine statements from one issuer look like (producer software, fonts,
page size, boilerplate lines). A candidate is then compared against it. Profiles are built from
samples you supply; none are bundled, because nothing here can be verified against a bank.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .models import Finding, Severity
from .pdfforensics import extract_lines, read_pdf_meta

PROFILE_VERSION = 1


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text)).strip().lower()


def _fonts(lines) -> set:
    return {f for ln in lines for w in ln["words"] for f, _ in w.get("fonts", ())}


def learn_profile(paths, name: str) -> dict:
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("at least one genuine PDF is required")
    producers, creators, fonts, sizes, line_sets = set(), set(), set(), set(), []
    for p in paths:
        meta = read_pdf_meta(p)
        producers.add(meta["/Producer"])
        creators.add(meta["/Creator"])
        _, _, lines, page_sizes = extract_lines(p)
        fonts |= _fonts(lines)
        sizes |= set(page_sizes)
        line_sets.append({_norm(ln["text"]) for ln in lines if len(ln["text"]) > 8})
    static = sorted(set.intersection(*line_sets)) if len(line_sets) >= 2 else []
    # Drop lines that are only numbers/placeholders.
    static = [s for s in static if re.search(r"[a-z]{4}", s)][:60]
    return {
        "version": PROFILE_VERSION, "name": name, "samples": len(paths),
        "producers": sorted(producers), "creators": sorted(creators),
        "fonts": sorted(fonts), "page_sizes": sorted(list(s) for s in sizes),
        "static_lines": static,
    }


def save_profile(profile: dict, path) -> None:
    Path(path).write_text(json.dumps(profile, indent=2))


def load_profile(path_or_obj) -> dict:
    if isinstance(path_or_obj, dict):
        profile = path_or_obj
    else:
        profile = json.loads(Path(path_or_obj).read_text())
    if profile.get("version") != PROFILE_VERSION:
        raise ValueError("unsupported profile version")
    return profile


def check_profile(profile: dict, path: Path, lines, page_sizes) -> list:
    findings: list = []
    name = profile.get("name", "profile")
    meta = read_pdf_meta(path)
    for key, label in (("producers", "/Producer"), ("creators", "/Creator")):
        known = profile.get(key) or []
        if known and meta[label] not in known:
            findings.append(Finding(
                Severity.MEDIUM, "profile-producer",
                f"{label[1:]} does not match any genuine {name} sample.",
                evidence=f"found {meta[label]!r}; genuine samples used {known}"))

    seen_sizes = {tuple(s) for s in profile.get("page_sizes", [])}
    bad = [s for s in page_sizes if seen_sizes and tuple(s) not in seen_sizes]
    if bad:
        findings.append(Finding(
            Severity.MEDIUM, "profile-page-size",
            "Page size differs from genuine samples.",
            evidence=f"found {sorted(set(bad))}; expected {sorted(seen_sizes)}"))

    known_fonts = set(profile.get("fonts", []))
    if known_fonts:
        odd = []
        for ln in lines:
            for w in ln["words"]:
                for f, _ in w.get("fonts", ()):
                    if f not in known_fonts:
                        odd.append((ln["page"], w["text"], f))
        if odd:
            numeric = any(re.search(r"\d", t) for _, t, _ in odd)
            ex = "; ".join(f"page {p} '{t}' in {f}" for p, t, f in odd[:5])
            findings.append(Finding(
                Severity.HIGH if numeric else Severity.MEDIUM, "profile-font",
                f"{len(odd)} word(s) use fonts never seen in genuine {name} statements.",
                evidence=ex))

    static = profile.get("static_lines", [])
    if static:
        present = {_norm(ln["text"]) for ln in lines}
        missing = [s for s in static if s not in present]
        if len(missing) / len(static) > 0.3:
            findings.append(Finding(
                Severity.MEDIUM, "profile-boilerplate",
                f"{len(missing)} of {len(static)} standard header/footer lines from genuine {name} "
                "statements are missing or altered.",
                evidence="; ".join(missing[:5])))
    return findings
