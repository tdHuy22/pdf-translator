"""Bench cả pipeline OCR của app trên 10 bài bench đã scan (samples/*-scan.pdf, tạo bằng scripts/make_scans.py).

Đáp án = khối chữ app trích từ lớp chữ của bản gốc (data/*.pdf, app.extract_blocks). Khối OCR của bản scan
(mặc định app.ocr_rapid: RapidOCR + PP-DocLayout) được so với đáp án:
  - chữ:     độ phủ / độ chính xác theo từ (không tính thứ tự) trên cả trang; độ giống theo thứ tự từ của từng
             cặp khối khớp nhau (bản dịch cần câu đúng thứ tự, không chỉ đủ từ)
  - đoạn:    khối đáp án được khớp đúng 1–1, bị chia nhỏ, bị gộp với khối khác, bị bỏ sót; khối OCR thừa
             (chữ trong hình/công thức/số trang mà bản gốc không dịch)
  - cỡ chữ:  sai số tương đối của cỡ chữ ước lượng (bản dịch in theo cỡ này)
  - tốc độ:  giây OCR mỗi trang (chỉ đúng khi --jobs 1)
Không gọi LLM. Kết quả OCR thô (dòng RapidOCR, vùng layout) được cache theo mã nguồn của hàm tạo ra nó
(bench/ocr/cache/): sửa phần gom đoạn thì chạy lại chỉ mất vài giây; sửa rapid_lines/detect_layout thì tự OCR lại.

  .venv/bin/python scripts/bench_ocr_pipeline.py                 # cả 10 bài
  .venv/bin/python scripts/bench_ocr_pipeline.py bert gqa -v     # vài bài, in từng lỗi
  .venv/bin/python scripts/bench_ocr_pipeline.py --no-cache --jobs 1   # đo tốc độ thật
  git show HEAD:app.py > app_before.py && .venv/bin/python scripts/bench_ocr_pipeline.py --app app_before.py --tag before
Kết quả lưu ở bench/ocr/results/, mỗi lần chạy tự so với lần trước.
"""
import argparse
import collections
import concurrent.futures as cf
import datetime
import difflib
import hashlib
import importlib.util
import inspect
import json
import pickle
import re
import statistics
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from bench_text import DATA, PAPERS  # noqa: E402

OUT = ROOT / "bench" / "ocr"
SCANS = ROOT / "samples"
TAGS = re.compile(r"</?[bi]>|\{v\d+\}")


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", TAGS.sub(" ", text).lower())


def _inter(a, b) -> float:
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(a) -> float:
    return max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)


class Cache:
    """Cache kết quả hàm theo (file, trang, tham số, mã nguồn hàm) — đổi code của hàm là tự tính lại."""

    def __init__(self, name: str, enabled: bool):
        self.path, self.enabled = OUT / "cache" / f"{name}.pkl", enabled
        self.data = pickle.loads(self.path.read_bytes()) if enabled and self.path.exists() else {}
        self.dirty = False

    def wrap(self, fn, timings: list | None = None):
        src = hashlib.sha1(inspect.getsource(fn).encode()).hexdigest()[:10]

        def run(page, *a, **k):
            key = (fn.__name__, src, Path(page.parent.name).name, page.number, a, tuple(sorted(k.items())))
            if key not in self.data:
                t = time.perf_counter()
                self.data[key] = fn(page, *a, **k)
                if timings is not None:
                    timings.append(time.perf_counter() - t)
                self.dirty = True
            return self.data[key]
        return run

    def save(self):
        if self.enabled and self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(pickle.dumps(self.data))


def lab(b: dict) -> str:
    return b.get("label") or "-"


def compare(truth: list[dict], got: list[dict]) -> dict:
    """So khối OCR với khối đáp án của một trang."""
    tw = collections.Counter(w for b in truth for w in words(b["text"]))
    gw = collections.Counter(w for b in got for w in words(b["text"]))
    hit = sum((tw & gw).values())
    # khối OCR thuộc khối đáp án giao với nó nhiều nhất, nếu phần giao ≥ 40% khối nhỏ hơn (ô chữ 5pt: khung OCR
    # cao gấp đôi chữ, lệch vài pt vì trang scan nghiêng)
    owner = []
    for g in got:
        best = max(((_inter(g["bbox"], t["bbox"]), i) for i, t in enumerate(truth)), default=(0, None))
        ok = best[1] is not None and best[0] >= 0.4 * min(_area(g["bbox"]), _area(truth[best[1]]["bbox"]))
        owner.append(best[1] if ok else None)
    kids = collections.defaultdict(list)
    for j, i in enumerate(owner):
        if i is not None:
            kids[i].append(j)
    # một khối OCR phủ ≥ 50% của ≥ 2 khối đáp án → gộp
    merged = {j for j, g in enumerate(got)
              if sum(_inter(g["bbox"], t["bbox"]) >= 0.5 * _area(t["bbox"]) for t in truth) >= 2}
    r = collections.Counter(truth=len(truth), got=len(got), words=sum(tw.values()), got_words=sum(gw.values()), hit=hit)
    errs, sims, sizes, labels = [], [], [], []
    for i, t in enumerate(truth):
        js = kids.get(i, [])
        if not js:
            if any(_inter(got[j]["bbox"], t["bbox"]) >= 0.5 * _area(t["bbox"]) for j in merged):
                r["merged"] += 1
                errs.append(("gộp", lab(t), t["text"][:70]))
            else:
                r["missed"] += 1
                errs.append(("sót", lab(t), t["text"][:70]))
        elif len(js) > 1:
            r["split"] += 1
            errs.append((f"chia {len(js)}", lab(t), t["text"][:70]))
        elif js[0] in merged:
            r["merged"] += 1
            errs.append(("gộp", lab(t), t["text"][:70]))
        else:
            g = got[js[0]]
            r["one"] += 1
            a, b = words(t["text"]), words(g["text"])
            sims.append(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a or b else 1.0)
            sizes.append(float(abs(g["size"] / t["size"] - 1)))
            labels.append(g.get("label") == t.get("label"))
            if g["bold"] != t["bold"]:
                r["bold_fp" if g["bold"] else "bold_fn"] += 1
            if sims[-1] < 0.8:
                errs.append((f"chữ {sims[-1]:.2f}", lab(t), f"{t['text'][:60]!r} ⇄ {g['text'][:60]!r}"))
    for j, g in enumerate(got):
        if owner[j] is None and j not in merged:
            r["extra"] += 1
            errs.append(("thừa", lab(g), g["text"][:70]))
    return {"n": dict(r), "sims": sims, "sizes": sizes, "labels": labels, "errs": errs}


def run_paper(name: str, use_cache: bool, engine: str, app_path: str = "") -> dict:
    import app  # đáp án luôn trích bằng app.py hiện tại, để hai phiên bản OCR được chấm như nhau
    mod = app
    if app_path:  # đo OCR của bản app.py khác (vd. bản trước khi sửa: git show HEAD:app.py > app_before.py)
        spec = importlib.util.spec_from_file_location("app_other", app_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    cache = Cache(Path(name).stem, use_cache)
    fresh: list[float] = []
    app.detect_layout = cache.wrap(app.detect_layout)
    truth_blocks = cache.wrap(app.extract_blocks)
    if mod is not app:
        mod.detect_layout = cache.wrap(mod.detect_layout)
    mod.rapid_lines = cache.wrap(mod.rapid_lines, fresh)
    ocr = getattr(mod, engine)
    src, scan = pymupdf.open(DATA / name), pymupdf.open(SCANS / (Path(name).stem + "-scan.pdf"))
    pages, ocr_secs, group_secs = [], [], []
    for n in range(len(src)):
        truth = truth_blocks(src[n])
        t = time.perf_counter()
        before = len(fresh)
        got = ocr(scan[n], "eng")
        # dòng OCR vừa đọc thật: cả trang tính là thời gian OCR; lấy từ cache: chỉ còn thời gian gom đoạn
        (ocr_secs if len(fresh) > before else group_secs).append(time.perf_counter() - t)
        pages.append(compare(truth, got))
    cache.save()
    return {"pages": pages, "ocr_secs": ocr_secs, "group_secs": group_secs}


def summarize(pages: list[dict]) -> dict:
    n = collections.Counter()
    sims, sizes, labels = [], [], []
    for p in pages:
        n.update(p["n"])
        sims += p["sims"]
        sizes += p["sizes"]
        labels += p["labels"]
    T = max(n["truth"], 1)
    return {
        "pages": len(pages),
        "recall": round(100 * n["hit"] / max(n["words"], 1), 2),
        "precision": round(100 * n["hit"] / max(n["got_words"], 1), 2),
        "one_to_one": round(100 * n["one"] / T, 1),
        "split": n["split"], "merged": n["merged"], "missed": n["missed"], "extra": n["extra"],
        "truth_blocks": n["truth"], "ocr_blocks": n["got"],
        "text_sim": round(100 * statistics.mean(sims), 2) if sims else 0,
        "text_sim_lt90": sum(s < 0.9 for s in sims),
        "size_err": round(100 * statistics.mean(sizes), 1) if sizes else 0,
        "size_err_gt10": sum(s > 0.1 for s in sizes),
        "label_ok": round(100 * statistics.mean(labels), 1) if labels else 0,
        "bold_fp": n["bold_fp"], "bold_fn": n["bold_fn"],
    }


COLS = [("recall", "phủ%"), ("precision", "đúng%"), ("text_sim", "giống%"), ("one_to_one", "1–1%"),
        ("split", "chia"), ("merged", "gộp"), ("missed", "sót"), ("extra", "thừa"),
        ("size_err", "cỡ±%"), ("label_ok", "nhãn%"), ("bold_fp", "đậm+"), ("bold_fn", "đậm−")]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("papers", nargs="*", help="một phần tên file, vd. bert gqa")
    ap.add_argument("--engine", default="ocr_rapid", help="hàm OCR trong app.py (page, lang) → khối")
    ap.add_argument("--app", default="", help="đo OCR của bản app.py khác (đáp án vẫn lấy từ app.py hiện tại)")
    ap.add_argument("--no-cache", action="store_true", help="OCR lại tất cả (đo tốc độ)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("-v", "--verbose", action="store_true", help="in từng lỗi")
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    names = [p for p in PAPERS if not args.papers or any(a in p for a in args.papers)]
    missing = [p for p in names if not (SCANS / (Path(p).stem + "-scan.pdf")).exists()]
    if missing:
        sys.exit(f"thiếu bản scan {missing}: chạy scripts/make_scans.py trước")
    t0 = time.time()
    with cf.ProcessPoolExecutor(max(1, min(args.jobs, len(names)))) as ex:
        res = dict(zip(names, ex.map(run_paper, names, [not args.no_cache] * len(names), [args.engine] * len(names),
                                     [str(Path(args.app).resolve()) if args.app else ""] * len(names))))
    print(f"{'bài':26}" + "".join(f"{h:>8}" for _, h in COLS))
    per = {}
    for name, r in res.items():
        s = per[Path(name).stem] = summarize(r["pages"])
        print(f"{Path(name).stem:26}" + "".join(f"{s[k]:>8}" for k, _ in COLS))
        if args.verbose:
            for i, p in enumerate(r["pages"]):
                for kind, label, text in p["errs"]:
                    print(f"    tr.{i + 1:<3} {kind:9} {label:16} {text}")
    total = summarize([p for r in res.values() for p in r["pages"]])
    print(f"{'TỔNG':26}" + "".join(f"{total[k]:>8}" for k, _ in COLS))
    ocr_secs = [s for r in res.values() for s in r["ocr_secs"]]
    group = [s for r in res.values() for s in r["group_secs"]]
    if ocr_secs:
        total["ocr_sec_page"] = round(statistics.mean(ocr_secs), 2)
        print(f"OCR {total['ocr_sec_page']} s/trang ({len(ocr_secs)} trang chưa có cache"
              + (", chạy song song nên chậm hơn thật)" if args.jobs > 1 else ")"))
    if group:
        total["group_sec_page"] = round(statistics.mean(group), 3)
        print(f"gom đoạn {total['group_sec_page']} s/trang")
    print(f"khối đáp án {total['truth_blocks']}, khối OCR {total['ocr_blocks']}, "
          f"cặp 1–1 giống < 90%: {total['text_sim_lt90']}, cỡ chữ lệch > 10%: {total['size_err_gt10']} — {time.time() - t0:.0f}s")

    runs = OUT / "results"
    prev = sorted(runs.glob("*.json")) if runs.exists() else []
    if prev:
        old = json.loads(prev[-1].read_text())
        if old.get("papers") == list(per):
            o = old["total"]
            print(f"so với {prev[-1].name}: " + (", ".join(
                f"{h} {o.get(k)}→{total[k]}" for k, h in COLS if o.get(k) != total[k]) or "không đổi"))
    if not args.no_save:
        runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.tag}" if args.tag else "")
        (runs / f"{stamp}.json").write_text(json.dumps(
            {"engine": args.engine, "papers": list(per), "total": total, "per_paper": per}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
