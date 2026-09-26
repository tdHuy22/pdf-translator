"""Tạo bản "scan" cho 10 bài bench (data/*.pdf trong scripts/bench_text.py) → samples/<tên>-scan.pdf.

Giống scan máy văn phòng: 200 dpi, ảnh xám, giấy hơi ngả màu, lệch trang nhẹ (±0,6°), nhiễu hạt, JPEG q=60.
Mỗi bài một seed cố định (theo tên file) → chạy lại ra đúng ảnh, bench so được giữa các lần.
Bản gốc có lớp chữ ở data/ vẫn là ĐÁP ÁN (xem scripts/bench_ocr_pipeline.py).

  .venv/bin/python scripts/make_scans.py            # tạo file còn thiếu
  .venv/bin/python scripts/make_scans.py --force    # tạo lại tất cả
"""
import argparse
import io
import sys
import zlib
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from bench_text import DATA, PAPERS  # noqa: E402

OUT = ROOT / "samples"


def scan_page(page: pymupdf.Page, rng: np.random.Generator, dpi: int, quality: int) -> bytes:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    # lệch trang: xoay quanh tâm, phần lộ ra ở góc là nền giấy
    img = img.rotate(float(rng.uniform(-0.6, 0.6)), resample=Image.BICUBIC, fillcolor=255)
    a = np.asarray(img, np.float32)
    # mực không đen tuyệt đối, giấy hơi xám; thêm nhiễu hạt
    a = 22 + a * (236 - 22) / 255 + rng.normal(0, 7, a.shape)
    buf = io.BytesIO()
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def make(src: Path, dst: Path, dpi: int, quality: int) -> None:
    rng = np.random.default_rng(zlib.crc32(src.name.encode()))
    doc, new = pymupdf.open(src), pymupdf.open()
    for page in doc:
        p = new.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=scan_page(page, rng, dpi, quality))
    new.save(dst, garbage=3, deflate=True)
    print(f"{dst.relative_to(ROOT)}: {len(new)} trang, {dst.stat().st_size / 1e6:.1f} MB", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--jpeg", type=int, default=60)
    ap.add_argument("--force", action="store_true", help="tạo lại cả file đã có")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    for name in PAPERS:
        dst = OUT / (Path(name).stem + "-scan.pdf")
        if args.force or not dst.exists():
            make(DATA / name, dst, args.dpi, args.jpeg)


if __name__ == "__main__":
    main()
