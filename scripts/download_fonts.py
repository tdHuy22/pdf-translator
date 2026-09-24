"""Tải các font mã nguồn mở (giấy phép OFL) hỗ trợ đầy đủ tiếng Việt vào thư mục fonts/.

Chạy: .venv/bin/python scripts/download_fonts.py
"""
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent.parent / "fonts"
GH = "https://github.com"
FILES = {
    # Source Serif 4 (Adobe) – font có chân hiện đại, rất hợp văn bản học thuật
    **{f"SourceSerif4-{s}.ttf": f"{GH}/adobe-fonts/source-serif/raw/release/TTF/SourceSerif4-{s}.ttf"
       for s in ("Regular", "Bold", "It", "BoldIt")},
    # Noto Serif / Noto Sans (Google) – phủ Unicode rộng, dấu tiếng Việt chuẩn
    **{f"NotoSerif-{s}.ttf": f"{GH}/notofonts/notofonts.github.io/raw/main/fonts/NotoSerif/hinted/ttf/NotoSerif-{s}.ttf"
       for s in ("Regular", "Bold", "Italic", "BoldItalic")},
    **{f"NotoSans-{s}.ttf": f"{GH}/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-{s}.ttf"
       for s in ("Regular", "Bold", "Italic", "BoldItalic")},
    # Be Vietnam Pro – font không chân thiết kế riêng cho tiếng Việt
    **{f"BeVietnamPro-{s}.ttf": f"{GH}/google/fonts/raw/main/ofl/bevietnampro/BeVietnamPro-{s}.ttf"
       for s in ("Regular", "SemiBold", "Italic", "SemiBoldItalic")},
}

DEST.mkdir(exist_ok=True)
with httpx.Client(follow_redirects=True, timeout=60) as client:
    for name, url in FILES.items():
        path = DEST / name
        if path.exists():
            print("✓ đã có ", name)
            continue
        r = client.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"↓ {name} ({len(r.content) // 1024} KB)")
