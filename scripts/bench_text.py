"""Bench bố cục cho PDF có lớp chữ: trích đoạn/công thức trên 10 bài báo LLM ngắn (~150 trang) trong data/ và đếm các lỗi đã biết
(ô bảng bị dính, đoạn sai kiểu chữ, dòng công thức bị trộn thành chữ vụn, mất gạch nối thật).
Không gọi LLM nên kết quả chỉ phụ thuộc code trích chữ → so được trước/sau khi sửa.

Chạy:
  .venv/bin/python scripts/bench_text.py              # trích chữ + đếm lỗi (~1 giây/trang)
  .venv/bin/python scripts/bench_text.py --render     # thêm: dựng "bản dịch" = chữ gốc cho mọi trang, đo thời gian/dung lượng
  .venv/bin/python scripts/bench_text.py --png        # thêm: lưu ảnh bản dựng các trang có bảng vào bench/png/
  .venv/bin/python scripts/bench_text.py --download   # tải lại bài còn thiếu từ arXiv
  .venv/bin/python scripts/bench_text.py bert lora    # chỉ chạy vài bài; --no-save: không lưu kết quả
  .venv/bin/python scripts/bench_text.py --app app_cu.py --tag before   # đo bản app.py khác (để trong thư mục dự án)
Kết quả lưu ở bench/results/, mỗi lần chạy tự so với lần trước.
"""
import argparse
import collections
import datetime
import importlib.util
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
app = None  # nạp trong main(): app.py của dự án, hoặc bản khác qua --app để so hai phiên bản

# tên file trong data/ → (arXiv id, bài)
PAPERS = {
    "attention-is-all-you-need.pdf": ("1706.03762v7", "Attention Is All You Need (2017)"),
    "bert.pdf": ("1810.04805v2", "BERT (2018)"),
    "roberta.pdf": ("1907.11692v1", "RoBERTa (2019)"),
    "rag.pdf": ("2005.11401v4", "Retrieval-Augmented Generation (2020)"),
    "roformer-rope.pdf": ("2104.09864v5", "RoFormer / RoPE (2021)"),
    "gqa.pdf": ("2305.13245v3", "GQA (2023)"),
    "flashattention-2.pdf": ("2307.08691v1", "FlashAttention-2 (2023)"),
    "mistral-7b.pdf": ("2310.06825v1", "Mistral 7B (2023)"),
    "mixtral.pdf": ("2401.04088v1", "Mixtral of Experts (2024)"),
    "datasentinel.pdf": ("2504.11358v4", "DataSentinel (2025)"),
}
DATA = ROOT / "data"
OUT = ROOT / "bench"
BIG_OP = re.compile(r"CMEX|(N?TX|PX)EX", re.I)  # glyph căn/Σ lớn: toạ độ chân chữ không nằm trên dòng
PLACEHOLDER_RE = re.compile(r"\{v\d+\}")
GARBLE = re.compile(r"(?:[A-Za-z]{1,2}\{v\d+\}){3,}")  # "p{v1}o{v2}s{v3}": chữ bị xé vụn giữa các công thức


def download():
    with httpx.Client(follow_redirects=True, timeout=600, headers={"User-Agent": "VietPaper-bench/1.0"}) as client:
        for name, (aid, title) in PAPERS.items():
            path = DATA / name
            if not path.exists():
                print("tải", title)
                r = client.get(f"https://arxiv.org/pdf/{aid}")
                r.raise_for_status()
                path.write_bytes(r.content)
                time.sleep(3)  # arXiv yêu cầu tải chậm


def page_chars(page) -> list[tuple]:
    """Ký tự của lớp chữ đọc thẳng từ PyMuPDF (không qua code gom chữ của app): (ký tự, span)."""
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            if abs(ln["dir"][1]) <= 0.1:
                out += [(c, s) for s in ln["spans"] for c in s["chars"] if not c["c"].isspace()]
    return out


def inside(c, r) -> bool:
    x0, y0, x1, y1 = c["bbox"]
    return r[0] <= (x0 + x1) / 2 <= r[2] and r[1] <= (y0 + y1) / 2 <= r[3]


def merged_cells(chars, size) -> bool:
    """Khối trong bảng có hàng chứa khoảng trống > 1 cỡ chữ → dính nhiều ô/cột."""
    rows: list[list] = []
    for c, s in sorted(chars, key=lambda cs: cs[0]["origin"][1]):
        if rows and abs(c["origin"][1] - rows[-1][0]) < 0.5 * size:
            rows[-1][1].append(c)
        else:
            rows.append([c["origin"][1], [c]])
    for _, cs in rows:
        cs.sort(key=lambda c: c["bbox"][0])
        if any(b["bbox"][0] - a["bbox"][2] > size for a, b in zip(cs, cs[1:])):
            return True
    return False


def is_bold(s) -> bool:  # cùng định nghĩa với app.text_block
    return bool(s["flags"] & 16) or "Bold" in s["font"]


def is_italic(s) -> bool:
    return bool(s["flags"] & 2) or "Italic" in s["font"]


def styled_fraction(text: str, bold: bool, italic: bool) -> tuple[float, float]:
    """Tỉ lệ chữ cái in đậm / nghiêng theo cách app sẽ in khối: kiểu nền của khối + thẻ <b>/<i> giữa đoạn."""
    n = nb = ni = 0
    depth = collections.Counter()
    for tok in re.split(r"(</?[bi]>|\{v\d+\})", text):
        if re.fullmatch(r"</?[bi]>", tok):
            depth[tok[-2]] += -1 if tok[1] == "/" else 1
            continue
        k = sum(ch.isalpha() for ch in tok) if not tok.startswith("{v") else 0
        n += k
        nb += k if bold or depth["b"] > 0 else 0
        ni += k if italic or depth["i"] > 0 else 0
    return (nb / n, ni / n) if n else (0.0, 0.0)


def hyphen_pairs(doc) -> list[tuple]:
    """Chỗ gạch nối cuối dòng có đáp án rõ: (trang, trước, sau, phải giữ gạch?). Phải giữ = chỗ khác trong bài chỉ
    viết "a-b" (English-to, heuristic-based) hoặc hai bên là số/chữ (LLaMA3-8B); phải bỏ = chỗ khác chỉ viết liền "ab"
    (PromptGuard). Bài viết cả hai kiểu, hoặc không có chỗ nào khác để so → không tính."""
    pages = [p.get_text() for p in doc]
    whole = "\n".join(pages)
    out = []
    for pno, t in enumerate(pages):
        for m in re.finditer(r"(\w+)-\n(\w+)", t):
            a, b = m[1], m[2]
            rest = whole.replace(m[0], " ")
            hyphened = len(re.findall(rf"(?<!\w){re.escape(a)}-{re.escape(b)}(?!\w)", rest, re.I))
            joined = len(re.findall(rf"(?<!\w){re.escape(a + b)}(?!\w)", rest, re.I))
            if hyphened and not joined:
                out.append((pno, a, b, True))
            elif joined and not hyphened:
                out.append((pno, a, b, False))
            elif not hyphened and not joined and (a[-1].isdigit() or b[0].isdigit()):
                out.append((pno, a, b, True))
    return out


def cached_translations(stem: str) -> dict[int, dict[str, str]]:
    """Bản dịch thật đã lưu bởi scripts/bench_llm.py: {trang: {chữ gốc: bản dịch}} (bản B = cách gọi model mới)."""
    out = {}
    for f in (ROOT / "bench" / "translations").glob(f"{stem}-p*-B.json"):
        d = json.loads(f.read_text())
        out[d["page"] - 1] = {it["text"]: it["vi"] for it in d["items"] if it["vi"]}
    return out


def bench_paper(path: Path, render: bool, png: bool) -> dict:
    src = pymupdf.open(path)
    vi_pages = cached_translations(path.stem)
    out = pymupdf.open(path) if render else None
    cfg = app.Config()
    m = collections.Counter()
    examples = collections.defaultdict(list)
    t_extract = t_render = 0.0

    last = {}
    detect = app.detect_layout

    def spy_layout(page, threshold=0.5):  # lấy lại vùng layout mà extract_blocks vừa dùng, khỏi chạy model 2 lần
        last["regions"] = detect(page, threshold)
        return last["regions"]

    rows_of = app.rows_of

    def spy_rows(lines):  # hàng có chữ cỡ thường nằm cách chân chữ chính > 0,8 cỡ → hai dòng bị trộn làm một
        rows = rows_of(lines)
        for r in rows:
            main = collections.Counter((round(s["origin"][1], 1), round(s["size"], 1)) for ln in r for s in ln["spans"]
                                       if not app.MATH_FONT.search(s["font"])
                                       and sum(not c["c"].isspace() for c in s["chars"]) >= 3)
            if not main:
                continue
            base, size = main.most_common(1)[0][0]
            if any(not BIG_OP.search(s["font"]) and s["size"] >= 0.8 * size and abs(s["origin"][1] - base) > 0.8 * size
                   and any(not c["c"].isspace() for c in s["chars"]) for ln in r for s in ln["spans"]):
                m["mixed_rows"] += 1
        return rows

    app.detect_layout, app.rows_of = spy_layout, spy_rows
    try:
        page_texts = {}
        for pno, page in enumerate(src):
            t = time.perf_counter()
            blocks = app.extract_blocks(page)
            t_extract += time.perf_counter() - t
            regions = last.get("regions") or []
            tables = [g["bbox"] for g in regions if g["label"] == "table"]
            chars = page_chars(page)
            m["pages"] += 1
            m["blocks"] += len(blocks)
            m["formulas"] += sum(len(b["formulas"]) for b in blocks)
            m["chars"] += len(chars)
            m["tables"] += len(tables)
            page_texts[pno] = " ".join(re.sub(r"</?[bi]>", "", b["text"]) for b in blocks)
            covered = set()
            for b in blocks:
                own = [(c, s) for c, s in chars if inside(c, b["bbox"])]
                covered |= {id(c) for c, _ in own}
                if any(app._inter(b["bbox"], t) > 0.5 * app._area(b["bbox"]) for t in tables):
                    m["table_blocks"] += 1
                    if merged_cells(own, b["size"]):
                        m["merged_cells"] += 1
                        examples["merged_cells"].append(f"p{pno + 1}: {b['text'][:70]}")
                # kiểu chữ: tỉ lệ chữ cái đậm/nghiêng app sẽ in (kiểu nền + thẻ) lệch bản gốc > 20% chữ của khối
                plain = [s for c, s in own if c["c"].isalpha() and not app.MATH_FONT.search(s["font"])]
                if plain:
                    fb, fi = styled_fraction(b["text"], b["bold"], b["italic"])
                    if abs(fb - sum(map(is_bold, plain)) / len(plain)) > 0.2 \
                            or abs(fi - sum(map(is_italic, plain)) / len(plain)) > 0.2:
                        m["wrong_style"] += 1
                        examples["wrong_style"].append(f"p{pno + 1}: {'B' if b['bold'] else ''}"
                                                       f"{'I' if b['italic'] else ''} {b['text'][:70]}")
                if b["text"].count("<b>") + b["text"].count("<i>"):
                    m["inline_styles"] += 1
                if g := GARBLE.search(re.sub(r"</?[bi]>", "", b["text"])):
                    m["garbled"] += 1
                    examples["garbled"].append(f"p{pno + 1}: …{b['text'][max(0, g.start() - 20):g.end() + 20]}…")
            m["chars_in_blocks"] += len(covered)

            if render:
                t = time.perf_counter()
                # bố cục: dùng bản dịch thật nếu trang có trong bench/translations, không thì in lại chữ gốc
                vi = vi_pages.get(pno, {})
                tr = {i: vi.get(b["text"], b["text"]) for i, b in enumerate(blocks)}
                try:
                    info = app.render_translation(out[pno], blocks, tr, cfg, src) or {}
                    # nội dung trượt sang khung khối khác (lỗi ngắt trang của Story): trong ~10 chữ đầu của khung phải
                    # có ít nhất một trong 3 chữ đầu bản dịch của khối đó (bỏ qua ký hiệu công thức, dấu chú thích)
                    words = out[pno].get_text("words")
                    for i, v in info.items():
                        want = re.findall(r"[^\W\d_]{2,}", PLACEHOLDER_RE.sub(" ", re.sub(r"</?[bi]>", "", tr[i])))[:3]
                        r = pymupdf.Rect(v["rect"])
                        got = sorted((w for w in words if r.contains(pymupdf.Rect(w[:4]).tl + (1, 1))),
                                     key=lambda w: (round(w[3]), w[0]))
                        seen = [t for w in got[:10] for t in re.findall(r"[^\W\d_]{2,}", w[4])]
                        if want and seen and not set(want) & set(seen):
                            m["misplaced"] += 1
                            examples["misplaced"].append(f"p{pno + 1}: khung '{' '.join(want)}…' chứa '{' '.join(seen[:3])}…'")
                    for i, v in info.items():
                        w = len(re.sub(r"</?[bi]>", "", tr[i]))
                        m["lay_chars"] += w
                        m["lay_scale"] += v["scale"] * w
                        m["lay_small"] += w * (v["scale"] < 0.85)
                        if vi:
                            m["vi_chars"] += w
                            m["vi_scale"] += v["scale"] * w
                except Exception as e:  # noqa: BLE001
                    m["render_errors"] += 1
                    examples["render_errors"].append(f"p{pno + 1}: {e!r}"[:120])
                t_render += time.perf_counter() - t
                if png and tables:
                    (OUT / "png").mkdir(parents=True, exist_ok=True)
                    out[pno].get_pixmap(dpi=100).save(OUT / "png" / f"{path.stem}-p{pno + 1}.png")
    finally:
        app.detect_layout, app.rows_of = detect, rows_of

    for pno, a, b, real in hyphen_pairs(src):
        text = page_texts.get(pno, "")
        if real and re.search(rf"(?<!\w){re.escape(a + b)}(?!\w)", text):
            m["lost_hyphens"] += 1
            examples["lost_hyphens"].append(f"p{pno + 1}: {a}-{b} → {a}{b}")
        elif not real and re.search(rf"(?<!\w){re.escape(a)}-{re.escape(b)}(?!\w)", text):
            m["extra_hyphens"] += 1
            examples["extra_hyphens"].append(f"p{pno + 1}: {a}{b} → {a}-{b}")

    res = dict(m)
    res["extract_s_per_page"] = round(t_extract / max(m["pages"], 1), 2)
    if m["lay_chars"]:
        res["size_pct"] = round(100 * m["lay_scale"] / m["lay_chars"], 1)
        res["small_pct"] = round(100 * m["lay_small"] / m["lay_chars"], 1)
    if m["vi_chars"]:
        res["vi_size_pct"] = round(100 * m["vi_scale"] / m["vi_chars"], 1)
    if render:
        buf = io.BytesIO()  # như lúc tải về (app.download): gộp stream trùng + cắt font
        final = pymupdf.open("pdf", out.tobytes(garbage=4))
        final.subset_fonts()
        final.save(buf, garbage=4, deflate=True)
        res["render_s_per_page"] = round(t_render / max(m["pages"], 1), 2)
        res["size_kb"] = len(buf.getvalue()) // 1024
        res["orig_kb"] = path.stat().st_size // 1024
    res["examples"] = {k: v[:5] for k, v in examples.items()}
    return res


COLS = [("pages", "Trang"), ("blocks", "Đoạn"), ("formulas", "C.thức"), ("tables", "Bảng"),
        ("merged_cells", "Ô dính"), ("wrong_style", "Sai kiểu"), ("inline_styles", "Có thẻ b/i"),
        ("mixed_rows", "Hàng trộn"), ("garbled", "Chữ vụn"),
        ("lost_hyphens", "Mất gạch"), ("extra_hyphens", "Thừa gạch"), ("coverage", "% chữ"),
        ("extract_s_per_page", "s/trang")]
RENDER_COLS = [("render_errors", "Lỗi dựng"), ("misplaced", "Khối lệch khung"), ("size_pct", "Cỡ chữ % gốc"), ("small_pct", "% chữ co >15%"),
               ("vi_size_pct", "Cỡ chữ % gốc (bản dịch thật)"),
               ("render_s_per_page", "s dựng/tr"), ("size_kb", "KB"), ("orig_kb", "KB gốc")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="không lưu kết quả (chạy thử)")
    ap.add_argument("--app", default=str(ROOT / "app.py"), help="đo phiên bản app.py khác (đặt trong thư mục dự án)")
    ap.add_argument("--tag", default="", help="thêm vào tên file kết quả, vd. before/after")
    ap.add_argument("papers", nargs="*", help="chỉ chạy các bài này (tên file, không cần .pdf)")
    args = ap.parse_args()
    global app
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("app", args.app)
    app = sys.modules["app"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    if args.download:
        download()
    names = [n for n in PAPERS if not args.papers or Path(n).stem in args.papers]
    results = {}
    for name in names:
        path = DATA / name
        if not path.exists():
            print(f"thiếu {name} → chạy với --download")
            continue
        print(f"… {PAPERS[name][1]}", flush=True)
        r = bench_paper(path, args.render or args.png, args.png)
        r["coverage"] = round(100 * r.get("chars_in_blocks", 0) / max(r.get("chars", 1), 1), 1)
        results[Path(name).stem] = r

    cols = COLS + (RENDER_COLS if args.render or args.png else [])
    total = {k: sum(r.get(k, 0) for r in results.values()) for k, _ in cols}
    total["coverage"] = round(100 * sum(r.get("chars_in_blocks", 0) for r in results.values())
                              / max(sum(r.get("chars", 0) for r in results.values()), 1), 1)
    for k in ("extract_s_per_page", "render_s_per_page"):
        pages = sum(r["pages"] for r in results.values())
        total[k] = round(sum(r.get(k, 0) * r["pages"] for r in results.values()) / max(pages, 1), 2)
    for k, num, den in (("size_pct", "lay_scale", "lay_chars"), ("small_pct", "lay_small", "lay_chars"),
                        ("vi_size_pct", "vi_scale", "vi_chars")):
        d = sum(r.get(den, 0) for r in results.values())
        total[k] = round(100 * sum(r.get(num, 0) for r in results.values()) / d, 1) if d else 0

    prev_file = max(OUT.glob("results/*.json"), default=None) if OUT.exists() else None
    prev = json.loads(prev_file.read_text())["papers"] if prev_file else {}

    def cell(r, p, k):
        v = r.get(k, 0)
        if p and k in p and p[k] != v and k not in ("extract_s_per_page", "render_s_per_page"):
            return f"{v} ({'+' if v > p[k] else ''}{round(v - p[k], 1)})"
        return str(v)

    print("\n| Bài | " + " | ".join(h for _, h in cols) + " |")
    print("|---|" + "---:|" * len(cols))
    for name, r in results.items():
        print(f"| {name} | " + " | ".join(cell(r, prev.get(name), k) for k, _ in cols) + " |")
    prev_total = None
    if prev and set(prev) >= set(results):
        prev_total = {k: sum(prev[n].get(k, 0) for n in results) for k, _ in cols}
        prev_total.pop("coverage")
    print("| **Tổng** | " + " | ".join(cell(total, prev_total, k) for k, _ in cols) + " |")

    for name, r in results.items():
        for kind, ex in r["examples"].items():
            for e in ex[:3]:
                print(f"  [{name}] {kind}: {e}")

    if args.no_save:
        return
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.tag}" if args.tag else "")
    path = OUT / "results" / f"{stamp}.json"
    path.write_text(json.dumps({"papers": results, "total": total}, ensure_ascii=False, indent=1))
    print(f"\nĐã lưu {path.relative_to(ROOT)}" + (f" (so với {prev_file.name})" if prev_file else ""))


if __name__ == "__main__":
    main()
