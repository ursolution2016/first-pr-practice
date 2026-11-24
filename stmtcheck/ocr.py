"""OCR for scanned statements (Tesseract via pytesseract) and PDF page rendering."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image

from .parser import MONEY_RE
from .pdfforensics import lines_to_text

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def _find_tesseract():
    """Tesseract on PATH, or a copy shipped beside the app (tesseract/tesseract[.exe] with a
    tessdata folder), which is how the portable build is meant to be used."""
    import os
    import sys
    names = ("tesseract.exe", "tesseract") if os.name == "nt" else ("tesseract",)
    bases = []
    if getattr(sys, "frozen", False):
        bases += [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "."))]
    for base in bases:
        for name in names:
            cand = base / "tesseract" / name
            if cand.is_file():
                if (base / "tesseract" / "tessdata").is_dir():
                    os.environ.setdefault("TESSDATA_PREFIX", str(base / "tesseract" / "tessdata"))
                return str(cand)
    return shutil.which("tesseract")


def ocr_available() -> bool:
    try:
        import pytesseract
    except ImportError:
        return False
    found = _find_tesseract()
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
    return found is not None


def render_pdf_pages(path: Path, dpi: int = 300) -> list:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()


def load_images(path: Path, dpi: int = 300) -> list:
    if path.suffix.lower() == ".pdf":
        return render_pdf_pages(path, dpi)
    img = Image.open(path)
    frames = []
    try:
        n = getattr(img, "n_frames", 1)
        for i in range(n):
            img.seek(i)
            frames.append(img.convert("RGB"))
    finally:
        img.close()
    return frames


_DIGIT_CFG = "--psm 7 -c tessedit_char_whitelist=0123456789.,-()CRDcr"
_COMMA_DECIMAL = re.compile(r"^(\(?-?\$?\d+(?:,\d{3})*),(\d{2}\)?)$")


def normalise_money_text(text: str) -> str:
    """OCR often turns the decimal point into a comma: '212,16' -> '212.16'."""
    m = _COMMA_DECIMAL.match(text)
    return f"{m[1]}.{m[2]}" if m else text


def _reread_figure(img, word):
    """Second OCR pass on a single figure with a digits-only alphabet."""
    import pytesseract
    from pytesseract import Output
    pad = 6
    crop = img.crop((max(word["x0"] - pad, 0), max(word["top"] - pad, 0),
                     word["x1"] + pad, word["bottom"] + pad))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    d = pytesseract.image_to_data(crop, output_type=Output.DICT, config=_DIGIT_CFG)
    toks = [(t, float(c)) for t, c in zip(d["text"], d["conf"]) if t.strip() and float(c) >= 0]
    if not toks:
        return
    text = normalise_money_text("".join(t for t, _ in toks))
    if MONEY_RE.fullmatch(text):
        word["text"] = text
        word["conf"] = sum(c for _, c in toks) / len(toks)


def ocr_lines(images: list) -> list:
    """OCR page images into line records with word boxes and confidences."""
    import pytesseract
    from pytesseract import Output

    lines = []
    for pno, img in enumerate(images, 1):
        d = pytesseract.image_to_data(img, output_type=Output.DICT,
                                      config="--psm 6 -c preserve_interword_spaces=1")
        grouped: dict = {}
        for i, txt in enumerate(d["text"]):
            txt = txt.strip()
            if not txt:
                continue
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            grouped.setdefault(key, []).append({
                "text": txt,
                "x0": d["left"][i], "x1": d["left"][i] + d["width"][i],
                "top": d["top"][i], "bottom": d["top"][i] + d["height"][i],
                "conf": float(d["conf"][i]),
            })
        page_lines = []
        for words in grouped.values():
            words.sort(key=lambda w: w["x0"])
            if len(words) >= 3:  # dated row: re-read the amount and balance figures
                for w in words[-2:]:
                    if any(ch.isdigit() for ch in w["text"]):
                        w["text"] = normalise_money_text(w["text"])
                        _reread_figure(img, w)
            page_lines.append({"page": pno, "text": " ".join(w["text"] for w in words),
                               "words": words, "source": "ocr",
                               "_y": median(w["top"] for w in words)})
        page_lines.sort(key=lambda ln: ln["_y"])
        lines += page_lines
    return lines


def median(vals):
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def ocr_to_text(images: list):
    lines = ocr_lines(images)
    text, page_of_line, lines = lines_to_text(lines)
    return text, page_of_line, lines
