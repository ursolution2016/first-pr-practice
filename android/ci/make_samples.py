"""Create the sample statements the emulator smoke test uploads (synthetic, fictional)."""
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stmtcheck import build_pdf  # noqa: E402
from stmtcheck.ocr import render_pdf_pages  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
build_pdf(out / "tampered.pdf", balance_override={2: Decimal("2000.00")})   # text layer
build_pdf(out / "clean.pdf")
build_pdf(out / "scan_source.pdf", balance_override={2: Decimal("2000.00")})
page = render_pdf_pages(out / "scan_source.pdf", 200)[0]
a = np.asarray(page, dtype=np.float32) + np.random.default_rng(1).normal(0, 3, (page.height, page.width, 1))
Image.fromarray(np.clip(a, 0, 255).astype("uint8")).save(out / "scan_tampered.jpg", quality=85)
print("samples written to", out)
