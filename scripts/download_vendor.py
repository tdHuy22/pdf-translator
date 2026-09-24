"""Tải thư viện hiển thị câu trả lời LLM (Markdown + công thức + bảng) vào static/vendor/.

Chạy: .venv/bin/python scripts/download_vendor.py

Vì sao để cục bộ chứ không gọi CDN: app chạy trên máy cá nhân và thường dùng kèm server LLM trong mạng nội bộ
(TUF qua Tailscale) — mất mạng ra ngoài thì vẫn phải đọc được bài. Dự án cũng đã theo lối tải font về `fonts/`.

Font của KaTeX chỉ giữ bản **woff2** (mọi trình duyệt còn dùng đều đọc được) và CSS được sửa lại cho bỏ hai
định dạng woff/ttf đi kèm: 3,2 MB xuống còn khoảng 1,1 MB mà hiển thị không đổi.
"""
import re
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent.parent / "static" / "vendor"
CDN = "https://cdn.jsdelivr.net/npm"
KATEX = "katex@0.16.11"
FILES = {
    "marked.min.js": f"{CDN}/marked@12.0.2/marked.min.js",            # Markdown → HTML (có bảng kiểu GFM)
    "purify.min.js": f"{CDN}/dompurify@3.1.6/dist/purify.min.js",     # lọc HTML: câu trả lời LLM là dữ liệu, không tin được
    "katex.min.js": f"{CDN}/{KATEX}/dist/katex.min.js",               # công thức
    "katex.min.css": f"{CDN}/{KATEX}/dist/katex.min.css",
    # không cần contrib/auto-render: static/render.js tự rút công thức ra TRƯỚC khi chạy Markdown (auto-render
    # chạy sau, lúc Markdown đã ăn mất `\` và `_` trong LaTeX)
}
# src:url(fonts/X.woff2) format("woff2"),url(fonts/X.woff) format("woff"),url(fonts/X.ttf) format("truetype")
DROP_FALLBACK = re.compile(r',url\([^)]+\.(?:woff|ttf)\)\s*format\("(?:woff|truetype)"\)')

DEST.mkdir(parents=True, exist_ok=True)
(DEST / "fonts").mkdir(exist_ok=True)
with httpx.Client(follow_redirects=True, timeout=120) as client:
    for name, url in FILES.items():
        path = DEST / name
        if path.exists():
            print("✓ đã có ", name)
            continue
        r = client.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"↓ {name} ({len(r.content) // 1024} KB)")

    css = (DEST / "katex.min.css").read_text()
    for font in sorted(set(re.findall(r"url\((fonts/[\w.-]+\.woff2)\)", css))):
        path = DEST / font
        if path.exists():
            continue
        r = client.get(f"{CDN}/{KATEX}/dist/{font}")
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"↓ {font} ({len(r.content) // 1024} KB)")

    if DROP_FALLBACK.search(css):
        (DEST / "katex.min.css").write_text(DROP_FALLBACK.sub("", css))
        print("✎ katex.min.css: bỏ nguồn font woff/ttf, chỉ giữ woff2")

total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
print(f"\nstatic/vendor/ – {len(list(DEST.rglob('*.woff2')))} font, tổng {total // 1024} KB")
