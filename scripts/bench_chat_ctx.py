"""Đo cách chọn ngữ cảnh khi trò chuyện: chế độ "Tự động" so với cách cũ ("near" = trang đang xem + lân cận,
chữ gốc + dịch — như app trước 25/09/2026) trên bench/chat_ctx.json.

Đi qua đúng endpoint thật (POST /api/<id>/chat, TestClient, thư mục lưu tạm — không đụng data/docs/). Bài chưa dịch
nên "gốc + dịch" chỉ có chữ gốc; không lập bảng thuật ngữ (khó cho việc đổi thuật ngữ Việt → Anh khi tìm trang).
Chấm bằng regex (bench/chat_ctx.json: "must"), không dùng LLM chấm: rẻ, lặp lại được, nhưng thô — câu trả lời đúng
mà diễn đạt khác đáp án thì bị chấm thiếu, nên xem thêm file kết quả.

    .venv/bin/python scripts/bench_chat_ctx.py                                   # dots-3-note-preview:free, cả hai chế độ
    .venv/bin/python scripts/bench_chat_ctx.py --model qwen/qwen3.8-27b:free --modes auto
    .venv/bin/python scripts/bench_chat_ctx.py --only 5,6                         # vài câu (số thứ tự từ 1)

Model mặc định đọc được ảnh nên chế độ Tự động gửi ảnh trang bằng chính model đó (cùng model cho cả hai chế độ).
Kết quả ghi vào bench/results/chat-ctx-<model>-<thời gian>.json sau mỗi lượt; huỷ giữa chừng thì chạy tiếp bằng
    .venv/bin/python scripts/bench_chat_ctx.py --resume bench/results/chat-ctx-….json"""
import argparse
import atexit
import json
import pathlib
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
ap.add_argument("--model", default="dots-studio/dots-3-note-preview:free")   # nex-n2.5-pro:free bị gỡ 25/09/2026
ap.add_argument("--see-model", default="", help="model xem ảnh (mặc định = --model)")
ap.add_argument("--modes", default="near,auto")
ap.add_argument("--only", default="", help="số thứ tự câu hỏi, vd. 1,5,6")
ap.add_argument("--kinds", default="", help="chỉ chạy các loại câu hỏi (kind), vd. table,figure")
ap.add_argument("--retries", type=int, default=3)
ap.add_argument("--budget", type=int, default=0,
                help="ngân sách token ngữ cảnh khi không hỏi cả bài (mặc định như app: CHAT_BUDGET). Bài ngắn chưa dịch "
                     "lọt cả vào 12k nên chế độ nào cũng gửi đủ bài — đặt nhỏ (vd. 3000, như model local 8k) để đo việc tìm trang")
ap.add_argument("--formula", default="off", choices=["off", "ocr", "see"],
                help="công thức → LaTeX (Config.formula_ocr): off = chép glyph như trước; ocr = model OCR (mặc định "
                     "PaddleOCR-VL qua Ollama local); see = model xem ảnh. Nhận dạng làm trước cho cả bài (không tính giờ)")
ap.add_argument("--no-see", action="store_true", help="không gửi ảnh trang — đo riêng phần chữ (vd. câu kind=latex)")
ap.add_argument("--resume", default="", help="file kết quả dở (bench/results/chat-ctx-….json): chỉ chạy các lượt còn thiếu")
args = ap.parse_args()

A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-bench-chat-"))
atexit.register(shutil.rmtree, A.store.ROOT, ignore_errors=True)   # xoá khi thoát, không để sót thư mục tạm
A.load_keys()
if args.budget:
    A.CHAT_BUDGET = args.budget
bench = json.loads((ROOT / "bench/chat_ctx.json").read_text())["questions"]
pick = {int(x) for x in args.only.split(",") if x.strip()}
kinds = {x.strip() for x in args.kinds.split(",") if x.strip()}
if kinds:
    pick = {k for k, q in enumerate(bench, 1) if q["kind"] in kinds and (not pick or k in pick)}
modes = [m.strip() for m in args.modes.split(",") if m.strip()]
cfg = {"base_url": args.base_url, "model": args.model, "ocr_mode": "off", "glossary_auto": False,
       "see_base_url": "" if args.no_see else args.base_url, "see_model": "" if args.no_see else args.see_model or args.model,
       "formula_ocr": args.formula}
if args.no_see:   # model trò chuyện đọc được ảnh thì app dùng luôn nó làm model xem ảnh (see_cfg) → quên nhãn model
    A.MODEL_META.clear()


def score(answer: str, must: list[str]) -> float:
    return sum(bool(re.search(p, answer, re.I)) for p in must) / len(must)


out = pathlib.Path(args.resume) if args.resume else \
    ROOT / "bench/results" / (f"chat-ctx-{args.model.replace('/', '_')}" + (f"-latex-{args.formula}" if args.formula != "off" else "")
                              + ("-nosee" if args.no_see else "") + f"-{time.strftime('%Y%m%d-%H%M%S')}.json")
rows = json.loads(out.read_text())["rows"] if args.resume else []
have = {(r["n"], r["mode"]) for r in rows}
with TestClient(A.app) as c:
    ids = {}
    for paper in sorted({q["paper"] for k, q in enumerate(bench, 1) if not pick or k in pick}):
        with open(ROOT / f"data/{paper}.pdf", "rb") as f:
            ids[paper] = c.post("/api/upload", files={"file": (f"{paper}.pdf", f, "application/pdf")}).json()["id"]
        d = A.DOCS[ids[paper]]
        for n in range(d["out"].page_count):   # đoạn + công thức của mọi trang như khi đã mở lớp chữ
            c.get(f"/api/{ids[paper]}/page/{n}/layer")
        if args.formula != "off":
            t = time.time()
            r = c.post(f"/api/{ids[paper]}/formulas", json={"cfg": cfg}).json()
            print(f"[công thức] {paper}: {r} — {time.time() - t:.0f} s", flush=True)
    for k, q in enumerate(bench, 1):
        if pick and k not in pick:
            continue
        for mode in modes:
            if (k, mode) in have:
                continue
            ans, info, secs = "", {}, 0.0
            for attempt in range(args.retries):
                t = time.time()
                r = c.post(f"/api/{ids[q['paper']]}/chat", json={"cfg": cfg, "question": q["q"], "page": q["page"],
                                                                 "context": mode})
                secs = time.time() - t
                ans, info = r.text, json.loads(r.headers.get("x-context") or "{}")
                if r.status_code == 200 and not ans.lstrip().startswith("⚠️"):
                    break
                print(f"   ↻ lỗi, thử lại: {ans.strip()[:120]}")
                time.sleep(10 * (attempt + 1))
            sc = score(ans, q["must"])
            rows.append({"n": k, "paper": q["paper"], "kind": q["kind"], "img": q.get("img"), "mode": mode, "q": q["q"], "score": sc,
                         "pages": info.get("pages", []), "images": info.get("images", []), "why": info.get("why", ""),
                         "model": info.get("model", ""), "secs": round(secs, 1), "answer": ans})
            print(f"{k:2} {mode:5} {sc:4.2f}  {secs:5.1f}s  tr.{info.get('pages', [])[:6]} ảnh{info.get('images', [])}"
                  f"  {q['q'][:60]}", flush=True)
            # ghi sau MỖI lượt: phải huỷ giữa chừng (model free treo) thì không mất gì, chạy tiếp bằng --resume
            out.write_text(json.dumps({"model": args.model, "formula": args.formula, "no_see": args.no_see, "rows": rows}, ensure_ascii=False, indent=1))

rows.sort(key=lambda r: (r["n"], r["mode"]))
print(f"\n{'':10}" + "".join(f"{m:>10}" for m in modes))
for kind in ["retrieval", "formula", "figure", "table", "global", "local", "trap", "latex", "*"]:
    sub = [r for r in rows if kind == "*" or r["kind"] == kind]
    if not sub:
        continue
    cells = []
    for m in modes:
        x = [r["score"] for r in sub if r["mode"] == m]
        cells.append(f"{sum(x) / len(x):10.2f}" if x else f"{'—':>10}")
    print(f"{'cả bộ' if kind == '*' else kind:10}" + "".join(cells) + f"   ({len(sub) // max(len(modes), 1)} câu)")
exp = [r for r in rows if r["mode"] == "auto" and r.get("img") is not None]
if exp and not args.no_see:   # chỉ hình mới cần ảnh trang; bảng (lưới chữ) và câu thường thì không
    bad = [r for r in exp if bool(r["images"]) != r["img"]]
    print(f"gửi ảnh đúng dự kiến: {len(exp) - len(bad)}/{len(exp)}" + (": sai " + ", ".join(
        f"#{r['n']} ({r['kind']}, {'có' if r['images'] else 'không'} ảnh)" for r in bad) if bad else ""))
eqs = [r for r in rows if r["kind"] == "formula" and r["mode"] == "auto"]
if eqs and not args.no_see:   # công thức: có LaTeX thì không gửi ảnh, chưa có thì gửi
    print("câu công thức: " + "; ".join(f"#{r['n']} {'có' if r['images'] else 'không'} ảnh ({r['why'][-45:]})" for r in eqs))
traps = [r for r in rows if r["kind"] == "trap" and r["mode"] == "auto"]
if traps:
    print(f"câu bẫy “mô hình”: {sum(bool(r['images']) for r in traps)}/{len(traps)} lần gửi ảnh (phải là 0)")
print("thời gian trung bình:", {m: round(sum(r["secs"] for r in rows if r["mode"] == m) / max(1, sum(r["mode"] == m for r in rows)), 1) for m in modes}, "s")
print("→", out.relative_to(ROOT))
