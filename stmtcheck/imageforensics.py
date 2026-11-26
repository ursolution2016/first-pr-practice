"""Image-level tamper indicators for scanned or photographed statements.

  * Noise (automatic, MEDIUM at most): background patches that are unnaturally smooth
    (white-out / digital fill) or much noisier than the rest of a scan.
  * EXIF (automatic): editing software and date inconsistencies.
  * ELA heatmap (human review only): written to disk on request. Automated ELA verdicts were
    tested and did not separate edited regions from clean scans reliably, so no findings are
    generated from it.
"""
from __future__ import annotations

import io
from collections import deque

import numpy as np
from PIL import ExifTags, Image

from .models import Finding, Severity
from .pdfforensics import EDITOR_MARKERS

BLOCK = 32


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def _blockify(a: np.ndarray, b: int = BLOCK) -> np.ndarray:
    ny, nx = a.shape[0] // b, a.shape[1] // b
    return a[:ny * b, :nx * b].reshape(ny, b, nx, b).swapaxes(1, 2)  # (ny, nx, b, b)


def _ink_fraction(gray: np.ndarray) -> np.ndarray:
    thr = np.median(gray) - 50
    return (_blockify(gray) < thr).mean(axis=(2, 3))


def ela_map(img: Image.Image, quality: int = 90) -> np.ndarray:
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    again = Image.open(buf).convert("RGB")
    diff = np.abs(np.asarray(rgb, dtype=np.int16) - np.asarray(again, dtype=np.int16))
    return diff.mean(axis=2).astype(np.float32)


def _clusters(mask: np.ndarray):
    ny, nx = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for y in range(ny):
        for x in range(nx):
            if mask[y, x] and not seen[y, x]:
                q, comp = deque([(y, x)]), []
                seen[y, x] = True
                while q:
                    cy, cx = q.popleft()
                    comp.append((cy, cx))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny_, nx_ = cy + dy, cx + dx
                            if 0 <= ny_ < ny and 0 <= nx_ < nx and mask[ny_, nx_] and not seen[ny_, nx_]:
                                seen[ny_, nx_] = True
                                q.append((ny_, nx_))
                out.append(comp)
    return out


def _region(comp, shape, b=BLOCK) -> str:
    ys = [c[0] for c in comp]
    xs = [c[1] for c in comp]
    h, w = shape
    return (f"x {min(xs) * b / w:.0%}-{(max(xs) + 1) * b / w:.0%}, "
            f"y {min(ys) * b / h:.0%}-{(max(ys) + 1) * b / h:.0%} of the page")


def _robust_z(vals: np.ndarray, floor: float) -> tuple:
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med))) * 1.4826
    return med, (vals - med) / max(mad, floor)


def ela_heatmap(img: Image.Image, quality: int = 90) -> Image.Image:
    """Amplified JPEG error-level image for visual inspection by a person."""
    ela = ela_map(img, quality)
    scale = 255.0 / max(float(np.percentile(ela, 99.5)), 1.0)
    return Image.fromarray(np.clip(ela * scale, 0, 255).astype("uint8"))


def check_noise(img: Image.Image, page: int) -> list:
    g = _gray(img)
    if min(g.shape) < BLOCK * 8:
        return []
    pad = np.pad(g, 1, mode="edge")
    resid = g - (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) / 4.0
    noise = _blockify(resid).std(axis=(2, 3))
    ink = _ink_fraction(g)
    bg = ink < 0.003
    if bg.sum() < 20:
        return []
    med = float(np.median(noise[bg]))
    if med < 0.8:  # digitally clean page: smoothness carries no information
        return []
    smooth = bg & (noise < 0.3 * med)
    rough = bg & (noise > 2.8 * med)
    findings = []
    for mask, label in ((smooth, "unnaturally smooth"), (rough, "much noisier")):
        for comp in _clusters(mask):
            if len(comp) < 3:
                continue
            findings.append(Finding(
                Severity.MEDIUM, "image-noise",
                f"A background area is {label} compared with the rest of the scan.",
                f"page {page}, {_region(comp, (noise.shape[0] * BLOCK, noise.shape[1] * BLOCK))}",
                f"{len(comp)} block(s); typical scan noise {med:.1f}. White-out, digital fills and "
                "pasted patches have a different noise signature from the paper around them."))
    return findings[:5]


def check_exif(path) -> list:
    try:
        img = Image.open(path)
        exif = img.getexif()
    except Exception:
        return []
    if not exif:
        return []
    tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
    try:
        tags.update({ExifTags.TAGS.get(k, k): v for k, v in exif.get_ifd(0x8769).items()})
    except Exception:
        pass
    findings = []
    software = str(tags.get("Software", ""))
    if any(m in software.lower() for m in EDITOR_MARKERS):
        findings.append(Finding(
            Severity.MEDIUM, "image-software",
            "Image metadata shows it was saved by an image or document editor.",
            evidence=f"Software={software!r}"))
    modified, original = tags.get("DateTime"), tags.get("DateTimeOriginal")
    if modified and original and str(modified) != str(original):
        findings.append(Finding(
            Severity.LOW, "image-dates",
            "Image modification time differs from the capture time.",
            evidence=f"captured {original}; modified {modified}"))
    return findings


def check_images(images: list) -> list:
    out = []
    for pno, img in enumerate(images, 1):
        out += check_noise(img, pno)
    return out
