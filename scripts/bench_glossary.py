"""Bench rút thuật ngữ: so danh sách thuật ngữ app rút ra (app.glossary_terms) với đáp án bench/glossary_gold.json
trên 6 bài ngắn trong data/ (4 bài dev để chỉnh luật, 2 bài test chỉ để kiểm tra).
Không gọi LLM nên chạy vài giây và kết quả chỉ phụ thuộc code rút thuật ngữ → so được trước/sau khi sửa.

Chỉ số mỗi bài:
  n      số thuật ngữ app rút ra
  prec   % thuật ngữ đúng (khớp gold hoặc ok) trên tổng số rút ra; trùng (cùng một mục đáp án lần 2) tính là sai
  recall % mục gold rút ra được
  junk   thuật ngữ không có trong đáp án (từ phổ thông, tên người, ký hiệu toán…)
  dup    thuật ngữ trùng mục đã có (models/model, The Transformer/transformer)
  r@80   % mục gold có trong 80 ứng viên đầu (app.term_candidates): trần độ phủ của bước LLM lọc + dịch

Chạy:
  .venv/bin/python scripts/bench_glossary.py              # đo, in thuật ngữ sai/sót, lưu vào bench/results/
  .venv/bin/python scripts/bench_glossary.py -v           # in thêm toàn bộ danh sách rút ra
  .venv/bin/python scripts/bench_glossary.py --check      # kiểm đáp án: mục gold nào gần như không xuất hiện trong bài
  .venv/bin/python scripts/bench_glossary.py gqa mixtral  # chỉ vài bài; --no-save: không lưu
  .venv/bin/python scripts/bench_glossary.py --app app_cu.py --tag before   # đo bản app.py khác
  .venv/bin/python scripts/bench_glossary.py --llm 4b     # đo cả bước LLM (app.build_glossary) trên router TUF:
                                                          # chấm các hàng LLM giữ lại, in cách dịch, thời gian
  .venv/bin/python scripts/bench_glossary.py --base-url https://openrouter.ai/api/v1 \
      --llm nvidia/nemotron-3-ultra-550b-a55b:free        # model cloud; key lấy từ data/keys.json
  .venv/bin/python scripts/bench_glossary.py --scan       # bản scan samples/<bài>-scan.pdf: chữ lấy từ khối OCR
                                                          # (app.scan_text) — so với chạy thường = bản PDF gốc
--scan dùng cache OCR của scripts/bench_ocr_pipeline.py (bench/ocr/cache/): chạy bench đó trước thì chỉ mất vài giây.
Chế độ --llm gọi LLM thật (~30–60 giây/bài với 4b), temperature 0; cách dịch từng hàng lưu trong file kết quả.
"""
import asyncio
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "bench" / "glossary_gold.json"
OUT = ROOT / "bench" / "results"


def singular(w: str) -> str:
    if len(w) > 5 and w.endswith(("trices", "dices", "tices")):   # matrices, indices, vertices
        return w[:-4] + ("x" if w.endswith("trices") else "ex")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is")) and not (len(w) > 4 and w.endswith("us")):
        return w[:-1]
    return w


def norm(term: str) -> str:
    """So khớp: không phân biệt hoa thường, gạch nối/gạch chéo = dấu cách, bỏ -s số nhiều ở mỗi chữ."""
    return " ".join(singular(w) for w in re.sub(r"[-_/]", " ", term.lower()).split())


def entries(items: list[str]) -> list[set[str]]:
    return [{norm(a) for a in it.split("|") if a.strip()} for it in items]


def is_abbr(term: str) -> bool:
    return " " not in term and sum(c.isupper() for c in term) >= 2


def score(terms: list[str], gold: list[str], ok: list[str]) -> dict:
    g, o = entries(gold), entries(ok)
    # Chữ viết tắt và dạng đầy đủ của cùng một mục (GQA + grouped-query attention) không tính là trùng: bảng thuật
    # ngữ cần cả hai. Trùng = hai dạng không viết tắt của cùng một mục (uptrained + uptraining).
    hit_g, hit_o, junk, dup, seen = set(), set(), [], [], set()
    for t in terms:
        k = norm(t)
        gi = next((i for i, e in enumerate(g) if k in e), None)
        oi = next((i for i, e in enumerate(o) if k in e), None)
        if gi is None and oi is None:
            junk.append(t)
            continue
        slot = ("g", gi) if gi is not None else ("o", oi)
        if (slot, is_abbr(t)) in seen or k in {norm(x) for x in terms[:terms.index(t)]}:
            dup.append(t)
        else:
            seen.add((slot, is_abbr(t)))
            (hit_g if gi is not None else hit_o).add(slot[1])
    n = len(terms)
    return {"n": n, "prec": round(100 * (len(hit_g) + len(hit_o)) / n, 1) if n else 0.0,
            "recall": round(100 * len(hit_g) / len(g), 1), "junk": junk, "dup": dup,
            "missed": [gold[i].split("|")[0].strip() for i in range(len(g)) if i not in hit_g]}


def body_text(doc) -> str:
    t = "\n".join(p.get_text(flags=pymupdf.TEXT_DEHYPHENATE) for p in doc)
    return t[:t.rfind("References")] if "References" in t else t


def check(name: str, spec: dict, doc) -> None:
    """Mục gold nào xuất hiện < 3 lần trong thân bài thì có lẽ không đáng là thuật ngữ cần thống nhất."""
    text = re.sub(r"[-\s]+", " ", body_text(doc).lower())
    for it in spec["gold"]:
        n = sum(len(re.findall(r"\b" + re.escape(re.sub(r"[-\s]+", " ", a.strip().lower())), text))
                for a in it.split("|"))
        if n < 3:
            print(f"  {name}: '{it}' chỉ xuất hiện {n} lần")


def scan_text(app, name: str, doc) -> tuple:
    """(PaperText, tên bài) của bản scan qua đúng đường của app (scan_text/title_of: OCR tới hết References), dòng OCR
    và vùng layout lấy từ cache của bench_ocr_pipeline."""
    import bench_ocr_pipeline as P
    orig = scan_text.orig = getattr(scan_text, "orig", None) or (app.rapid_lines, app.detect_layout)
    cache = P.Cache(name, True)
    app.rapid_lines, app.detect_layout = cache.wrap(orig[0]), cache.wrap(orig[1])
    app.store.save_state = lambda d: None   # tài liệu giả, không lưu
    d = {"src": doc.name, "blocks": {}, "marks": {}}
    text = asyncio.run(app.scan_text(d, app.Config(), doc))
    title = asyncio.run(app.title_of(d, doc))
    cache.save()
    return text, title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rescore", action="store_true", help="chấm lại mọi kết quả đã lưu theo đáp án hiện tại")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--app", default=str(ROOT / "app.py"))
    ap.add_argument("--tag", default="")
    ap.add_argument("--llm", default="", help="preset/model của router để đo cả bước LLM, vd. 4b")
    ap.add_argument("--base-url", default=os.environ.get("VP_TUF_URL", "http://localhost:8080/v1"), help="server LLM (mặc định router TUF)")
    ap.add_argument("--cands", type=int, default=0, help="--llm: số ứng viên gửi LLM (mặc định theo app)")
    ap.add_argument("--rows", type=int, default=0, help="--llm: số hàng tối đa của bảng (mặc định theo app)")
    ap.add_argument("--scan", action="store_true", help="đo trên bản scan (samples/<bài>-scan.pdf), chữ từ khối OCR")
    ap.add_argument("papers", nargs="*")
    args = ap.parse_args()
    gold = {k: v for k, v in json.loads(GOLD.read_text()).items()
            if not k.startswith("_") and (not args.papers or k in args.papers)}

    if args.rescore:
        print(f"{'kết quả':<86}{'dev prec/rec':>14}{'test prec/rec':>15}{'giây/bài':>9}")
        for f in sorted(OUT.glob("glossary*.json")):
            papers = json.loads(f.read_text())["papers"]
            row = []
            for split in ("dev", "test"):
                rs = [score(r["terms"], gold[k]["gold"], gold[k]["ok"]) for k, r in papers.items()
                      if k in gold and gold[k]["split"] == split]
                row.append(f"{sum(r['prec'] for r in rs) / len(rs):.1f}/{sum(r['recall'] for r in rs) / len(rs):.1f}"
                           if rs else "—")
            sec = sum(r["ms"] for r in papers.values()) / len(papers) / 1000
            print(f"{f.stem:<86}{row[0]:>14}{row[1]:>15}{sec:>9.1f}")
        return

    if args.check:
        for name, spec in gold.items():
            check(name, spec, pymupdf.open(ROOT / "data" / f"{name}.pdf"))
        return

    sys.path.insert(0, str(ROOT))
    spec_ = importlib.util.spec_from_file_location("app", args.app)
    app = sys.modules["app"] = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(app)
    app._uterms.update(blocked={}, prefer={})   # đo thuật toán, không để thuật ngữ người dùng đã dạy ảnh hưởng

    if args.cands:
        app.GLOSSARY_FIXED = args.cands
    if args.rows:
        app.GLOSSARY_ROWS = args.rows
    app.load_keys()   # key API đã lưu trong data/keys.json (vd. OpenRouter)
    cfg = app.Config(translator="llm", base_url=args.base_url, model=args.llm, temperature=0.0,
                     api_key=app._keys.get(args.base_url.rstrip("/"), "")) if args.llm else None
    results = {}
    for name, spec in gold.items():
        doc = pymupdf.open(ROOT / "data" / f"{name}.pdf")
        text = title = None
        if args.scan:
            doc = pymupdf.open(ROOT / "samples" / f"{name}-scan.pdf")
            text, title = scan_text(app, name, doc)
        t0 = time.perf_counter()
        rows = None
        if cfg:
            made = asyncio.run(app.build_glossary(cfg, doc, text, title))
            if made is None:
                print(f"{name}: gọi LLM lỗi")
                continue
            rows = made["rows"]
            terms = [r["term"] for r in rows]
        else:
            terms = app.glossary_terms(doc, text=text) if text else app.glossary_terms(doc)
        r = score(terms, spec["gold"], spec["ok"])
        r.update(split=spec["split"], ms=round(1000 * (time.perf_counter() - t0)), terms=terms)
        if rows is not None:
            r["rows"] = rows
        elif hasattr(app, "term_candidates"):
            cands = [c["term"] for c in app.term_candidates(doc, 80, text)]
            r80 = score(cands, spec["gold"], spec["ok"])
            r.update(r80=r80["recall"], missed80=r80["missed"])
        results[name] = r

    print(f"\n{'bài':<28}{'tập':<6}{'n':>4}{'prec':>7}{'recall':>8}{'junk':>6}{'dup':>5}{'r@80':>6}{'ms':>8}")
    for name, r in results.items():
        print(f"{name:<28}{r['split']:<6}{r['n']:>4}{r['prec']:>7}{r['recall']:>8}{len(r['junk']):>6}"
              f"{len(r['dup']):>5}{r.get('r80', ''):>6}{r['ms']:>8}")
    summary = {}
    for split in ("dev", "test"):
        rs = [r for r in results.values() if r["split"] == split]
        if rs:
            summary[split] = {k: round(sum(r.get(k, 0) for r in rs) / len(rs), 1) for k in ("prec", "recall", "r80")}
            print(f"{'  trung bình ' + split:<34}{summary[split]['prec']:>7}{summary[split]['recall']:>8}"
                  f"{summary[split]['r80']:>17}")

    for name, r in results.items():
        print(f"\n## {name}")
        if "rows" in r:
            print("  bảng   :", "; ".join(f"{x['term']} → {'=' if x['keep'] else x['vi']}" for x in r["rows"]))
        elif args.verbose:
            print("  rút ra :", ", ".join(r["terms"]))
        print("  sót    :", ", ".join(r["missed"]) or "—")
        if "missed80" in r:
            print("  sót@80 :", ", ".join(r["missed80"]) or "—")
        print("  rác    :", ", ".join(r["junk"]) or "—")
        if r["dup"]:
            print("  trùng  :", ", ".join(r["dup"]))

    kind = ("glossary-llm-" + re.sub(r"[^\w.-]+", "_", args.llm) if args.llm else "glossary") + ("-scan" if args.scan else "")
    prev = sorted(p for p in OUT.glob(f"{kind}-*.json") if (args.llm or "-llm-" not in p.name)
                  and (args.scan or "-scan-" not in p.name))
    if prev:
        old = json.loads(prev[-1].read_text()).get("summary", {})
        for split, s in summary.items():
            if split in old:
                print(f"\nso với {prev[-1].name} ({split}): prec {old[split]['prec']} → {s['prec']}, "
                      f"recall {old[split]['recall']} → {s['recall']}, r@80 {old[split].get('r80', '—')} → {s['r80']}")
    if not args.no_save:
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = OUT / f"{kind}-{stamp}{'-' + args.tag if args.tag else ''}.json"
        path.write_text(json.dumps({"summary": summary, "papers": results}, ensure_ascii=False, indent=1))
        print(f"\nlưu {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
