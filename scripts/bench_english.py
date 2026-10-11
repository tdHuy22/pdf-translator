"""Đo độ "sót tiếng Anh" của bản dịch: từ tiếng Anh thông dụng bị chép nguyên từ bản gốc sang bản dịch mà không nằm
trong bảng thuật ngữ (vd. "chúng tôi employ h = 8", "suspect", "Across"). Thuật ngữ giữ nguyên có chủ ý (attention,
embedding, softmax… — nằm trong bảng thuật ngữ) không tính.

  .venv/bin/python scripts/bench_english.py --tag base                        # dịch + chấm, lưu bench/english/base.json
  .venv/bin/python scripts/bench_english.py --tag rule2 --gloss-from base     # dùng lại bảng thuật ngữ của lượt base
  .venv/bin/python scripts/bench_english.py --rescore                         # chấm lại mọi lượt đã lưu

Chạy trong kho tạm (không đụng data/docs, data/user_terms.json). Mặc định OpenRouter free (dots-3-note-preview).
"""
import argparse
import atexit
import collections
import json
import pathlib
import re
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A  # noqa: E402

OUT = ROOT / "bench" / "english"
# Bài ngắn, nhiều văn xuôi kỹ thuật: Attention tr.3–5 (0-based 2–4), BERT tr.3–4, RoFormer tr.2
SETS = [("attention-is-all-you-need", [2, 3, 4]), ("bert", [2, 3]), ("roformer-rope", [1])]
# Âm tiết tiếng Việt viết không dấu ("trong", "cho", "theo", "thanh"…) trùng dạng chữ tiếng Anh → không tính
_VN = re.compile(r"(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?[aeiouy]{1,3}(ng|nh|ch|[cmnpt])?")
_WORD = re.compile(r"(?<![\w-])[A-Za-z][a-z]+(?:-[a-z]+)*(?![\w-])")


def english_left(src: str, vi: str, keep: set[str]) -> list[str]:
    """Từ tiếng Anh thông dụng có trong cả bản gốc lẫn bản dịch, không thuộc bảng thuật ngữ."""
    clean = lambda t: re.sub(r"\{v\d+\}|</?[bi]>|\[[\d,\s–-]+\]|https?://\S+", " ", t)
    have = {w.lower() for w in _WORD.findall(clean(src))}
    out = []
    for w in _WORD.findall(clean(vi)):
        low = w.lower()
        if low not in have or low in keep or A._singular(low) in keep or _VN.fullmatch(low):
            continue
        if len(low) >= 3 and A._word_zipf(low) >= 3.0:
            out.append(low)
    return out


def score(run: dict) -> dict:
    keep = set()
    for r in run["gloss"]:
        if r.get("use", True):
            keep |= {w.lower() for w in re.findall(r"[A-Za-z]+", r["term"] + " " + r.get("vi", ""))}
    left, words = collections.Counter(), 0
    for item in run["items"]:
        left.update(english_left(item["src"], item["vi"], keep))
        words += len(item["vi"].split())
    n = sum(left.values())
    return {"left": n, "words": words, "per100": round(100 * n / max(words, 1), 2), "top": left.most_common(25)}


def run_once(args) -> dict:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-english-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)     # xoá khi thoát (~10 MB mỗi lượt)
    A.store.ROOT, A.USER_TERMS_FILE, A._uterms = tmp, tmp / "user_terms.json", {}
    keys = json.loads((ROOT / "data" / "keys.json").read_text())
    A.KEYS_FILE = tmp / "keys.json"
    if args.rule:
        A.SYSTEM_PROMPT = A.SYSTEM_PROMPT.replace("\nInput is a JSON", f"\n- {args.rule}\nInput is a JSON")
    cfg = A.Config(base_url=args.base_url, api_key=keys.get(args.base_url, ""), model=args.model,
                   temperature=0.0, google_fallback=False, page_span=1).model_dump()
    frozen = json.loads((OUT / f"{args.gloss_from}.json").read_text())["gloss"] if args.gloss_from else None
    run = {"tag": args.tag, "model": args.model, "rule": args.rule, "gloss": [], "items": [], "time": 0.0}
    with A.test_client() as c:
        for name, pages in SETS:
            pdf = ROOT / "data" / f"{name}.pdf"
            doc = c.post("/api/upload", files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")}).json()["id"]
            d = A.get_doc(doc)
            if frozen is not None:
                d["title"] = A.doc_title(A.pymupdf.open(d["src"]))
                d["gloss"] = [{k: v for k, v in r.items() if k != "doc"} for r in frozen if r.get("doc") == name]
                d["gloss_extra"] = []
                A.apply_gloss(d)
            t = time.time()
            for n in pages:
                r = c.post(f"/api/{doc}/translate/{n}", json=cfg)
                if r.status_code != 200:
                    print(f"  {name} tr.{n + 1}: HTTP {r.status_code} {r.text[:200]}")
            run["time"] += time.time() - t
            run["gloss"] += [{**r, "doc": name} for r in d.get("gloss", [])]
            for n in pages:
                blocks = d["blocks"][n][1] if n in d["blocks"] else []
                for i, vi in d["trans"].get(n, {}).items():
                    if i < len(blocks):
                        run["items"].append({"doc": name, "page": n + 1, "id": i, "src": blocks[i]["text"], "vi": vi})
    return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base")
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--model", default="dots-studio/dots-3-note-preview:free")
    ap.add_argument("--rule", default="", help="thêm một luật vào cuối danh sách luật của prompt dịch")
    ap.add_argument("--gloss-from", default="", help="dùng lại bảng thuật ngữ của một lượt đã lưu (so sánh công bằng)")
    ap.add_argument("--rescore", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.rescore:
        run = run_once(args)
        (OUT / f"{args.tag}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1))
    for f in sorted(OUT.glob("*.json")):
        run = json.loads(f.read_text())
        s = score(run)
        print(f"{run['tag']:<14} {run['model'][:36]:<36} sót {s['left']:>3} / {s['words']} từ = {s['per100']:>5}/100 từ"
              f"  ({len(run['items'])} đoạn, {run['time']:.0f}s)")
        if f.stem == args.tag or args.rescore:
            print("   ", ", ".join(f"{w}×{k}" if k > 1 else w for w, k in s["top"]))


if __name__ == "__main__":
    main()
