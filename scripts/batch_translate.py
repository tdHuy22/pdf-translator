"""Dịch hàng loạt PDF bằng dòng lệnh, qua app đang chạy (./restart.sh start): bài dịch xong nằm luôn trong "Tài liệu
đã lưu" của giao diện, và chạy lại cùng file thì chỉ dịch tiếp các trang còn thiếu (app nhận ra file đã tải lên).

  .venv/bin/python scripts/batch_translate.py bai1.pdf bai2.pdf
  .venv/bin/python scripts/batch_translate.py ~/Papers/ --out ~/Papers/vi --format translated,dual-side
  .venv/bin/python scripts/batch_translate.py bai.pdf --pages 1-5,8 --model inclusionai/ling-3.0-flash-fin:free -j 2
  .venv/bin/python scripts/batch_translate.py bai.pdf --set temperature=0 --set skip_refs=false --force

Cấu hình dịch = mặc định của app (Config trong app.py), sửa bằng --model/--base-url/--translator, --config (file JSON
các trường của Config) hoặc --set trường=giá_trị. Key API không cần truyền: app lấy key đã nhớ theo base_url.
--format: translated (bản dịch), dual-side (song ngữ cạnh nhau), dual-alt (song ngữ xen trang), original (bản gốc có tô).
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys
import time

import httpx

# --format → (tham số tải về, đuôi tên file)
FORMATS = {"translated": ("which=translated", "_vi"), "dual-side": ("which=dual&layout=side", "_song_ngu"),
           "dual-alt": ("which=dual&layout=alt", "_song_ngu_xen_trang"), "original": ("which=original", "_goc_to_vang")}


def parse_range(text: str, pages: int) -> list[int]:
    """"1-3,5" (đánh số từ 1) → [0, 1, 2, 4]; rỗng = cả bài."""
    if not text.strip():
        return list(range(pages))
    out = set()
    for part in text.split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d*)\s*)?", part)
        if not m:
            raise ValueError(f"không hiểu dải trang {part!r}")
        a = int(m[1])
        b = (int(m[2]) if m[2] else pages) if m[2] is not None else a
        if not 1 <= a <= b <= pages:
            raise ValueError(f"dải {part.strip()!r} nằm ngoài 1–{pages}")
        out.update(range(a - 1, b))
    return sorted(out)


def pdf_files(paths: list[str]) -> list[pathlib.Path]:
    out = []
    for p in map(pathlib.Path, paths):
        p = p.expanduser()
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() == ".pdf" and not f.name.startswith("."))
        elif p.is_file():
            out.append(p)
        else:
            sys.exit(f"Không thấy {p}")
    return out


def value(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


async def build_config(c: httpx.AsyncClient, args) -> dict:
    """Ghép cấu hình và kiểm tên trường theo schema của app (gõ nhầm tên thì báo, không lặng lẽ bỏ qua)."""
    fields = (await c.get("/openapi.json")).json()["components"]["schemas"]["Config"]["properties"]
    cfg = json.loads(pathlib.Path(args.config).expanduser().read_text()) if args.config else {}
    for kv in args.set:
        k, sep, v = kv.partition("=")
        if not sep:
            sys.exit(f"--set cần dạng trường=giá_trị, không phải {kv!r}")
        cfg[k.strip()] = value(v.strip())
    for k in ("model", "base_url", "translator"):
        if getattr(args, k):
            cfg[k] = getattr(args, k)
    if bad := sorted(set(cfg) - set(fields)):
        sys.exit(f"Trường cấu hình không có: {', '.join(bad)}\nCó: {', '.join(sorted(fields))}")
    for k in ("base_url", "model", "translator"):   # để in ra: model thật sự sẽ dùng
        args.effective[k] = cfg.get(k, fields[k].get("default"))
    return cfg


async def translate_doc(c: httpx.AsyncClient, pdf: pathlib.Path, cfg: dict, args) -> int:
    """Tải lên, dịch các trang còn thiếu (hoặc mọi trang đã chọn nếu --force), tải bản dịch về. Trả về số lỗi."""
    r = await c.post("/api/upload", files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")})
    r.raise_for_status()
    up = r.json()
    doc, pages = up["id"], up["pages"]
    done = set((await c.get(f"/api/{doc}/state")).json()["done"])
    try:
        want = parse_range(args.pages, pages)
    except ValueError as e:
        print(f"  ✗ {e}")
        return 1
    queue = [n for n in want if args.force or n not in done]
    note = f"{len(want) - len(queue)} trang đã dịch từ trước, " if len(queue) < len(want) else ""
    print(f"{pdf.name}: {pages} trang, {note}cần dịch {len(queue)}" + (f" ({up['scanned_pages']} trang scan → OCR)"
                                                                       if up.get("scanned_pages") else ""))
    total, errors, t0 = len(queue), 0, time.time()
    left = set(queue)

    async def worker():
        nonlocal errors
        while queue:
            take = [queue.pop(0)]  # giữ chỗ các trang liền nhau để hai worker không giành nhau cùng một trang
            while len(take) < args.span and queue and queue[0] == take[-1] + 1:
                take.append(queue.pop(0))
            try:
                r = await c.post(f"/api/{doc}/translate/{take[0]}",
                                 params={"force": str(args.force).lower(), "span": len(take)}, json=cfg)
                if r.status_code != 200:
                    detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith(
                        "application/json") else r.text
                    raise RuntimeError(f"HTTP {r.status_code}: {str(detail)[:200]}")
                res = r.json()
            except (httpx.HTTPError, RuntimeError) as e:
                errors += 1
                left.discard(take[0])
                queue[:0] = take[1:]
                print(f"  ✗ tr.{take[0] + 1}: {e}")
                continue
            got = [p["page"] for p in res["pages"]]
            left.difference_update(got)
            queue[:0] = [n for n in take if n not in got]  # server gộp ít trang hơn → trả lại hàng đợi
            extra = [f"{res['via_google']} đoạn qua Google"] if res.get("via_google") else []
            extra += [f"lỗi: {res['errors'][0]}"] if res.get("errors") else []
            print(f"  tr.{'–'.join(str(p + 1) for p in (got[:1] + got[1:][-1:]))}"
                  f"  {total - len(left)}/{total}  {time.time() - t0:.0f}s" + (f"  ({'; '.join(extra)})" if extra else ""))

    await asyncio.gather(*(worker() for _ in range(max(1, args.jobs))))
    out = pathlib.Path(args.out).expanduser() if args.out else pdf.parent
    out.mkdir(parents=True, exist_ok=True)
    for f in args.format:
        query, suffix = FORMATS[f]
        r = await c.get(f"/api/{doc}/download?{query}")
        if r.status_code != 200:
            print(f"  ✗ tải {f}: HTTP {r.status_code}")
            errors += 1
            continue
        # đặt tên theo file đang dịch (app nhớ tên của lần tải lên đầu tiên, có thể khác)
        (path := out / f"{pdf.stem}{suffix}.pdf").write_bytes(r.content)
        print(f"  → {path}")
    return errors


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="file PDF hoặc thư mục (lấy mọi *.pdf ngay trong thư mục)")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--pages", default="", help='dải trang, đánh số từ 1: "1-5,8", "3-" (rỗng = cả bài)')
    ap.add_argument("--force", action="store_true", help="dịch lại cả trang đã dịch")
    ap.add_argument("--out", default="", help="thư mục lưu PDF (mặc định: cạnh file gốc)")
    ap.add_argument("--format", default="translated",
                    help=f"các bản cần tải, cách nhau dấu phẩy: {', '.join(FORMATS)} (mặc định translated)")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="số lượt gọi dịch chạy song song trong một bài")
    ap.add_argument("--span", type=int, default=3, help="số trang giữ chỗ cho một lượt gọi (server gộp được bao nhiêu thì gộp)")
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--translator", default="", choices=["", "llm", "mt", "google"])
    ap.add_argument("--config", default="", help="file JSON các trường cấu hình của app")
    ap.add_argument("--set", action="append", default=[], metavar="TRƯỜNG=GIÁ_TRỊ", help="đặt một trường cấu hình (lặp được)")
    args = ap.parse_args()
    args.effective = {}
    args.format = [f.strip() for f in args.format.split(",") if f.strip()]
    if bad := [f for f in args.format if f not in FORMATS]:
        sys.exit(f"--format không có: {', '.join(bad)} (có: {', '.join(FORMATS)})")
    files = pdf_files(args.paths)
    if not files:
        sys.exit("Không có file PDF nào")
    async with httpx.AsyncClient(base_url=args.server, timeout=httpx.Timeout(30, read=None)) as c:
        try:
            await c.get("/api/docs")
        except httpx.HTTPError:
            sys.exit(f"App chưa chạy ở {args.server} → ./restart.sh start")
        cfg = await build_config(c, args)
        shown = {**args.effective, **{k: v for k, v in cfg.items() if "key" not in k}}   # không in key API
        print("Cấu hình: " + ", ".join(f"{k}={v}" for k, v in shown.items()))
        errors = 0
        for pdf in files:
            try:
                errors += await translate_doc(c, pdf, cfg, args)
            except httpx.HTTPError as e:
                errors += 1
                print(f"  ✗ {pdf.name}: {e}")
    print(f"Xong {len(files)} bài" + (f", {errors} lỗi" if errors else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    asyncio.run(main())
