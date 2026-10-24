"""Thử THẬT mọi tính năng cần model khi nguồn model là Claude Code (gọi `claude -p` thật, tốn lượt của gói Claude trên
máy chạy app). Thư mục lưu tạm, không đụng data/docs/ hay data/keys.json. Khác scripts/test_claude.py (claude giả).

    .venv/bin/python scripts/live_claude.py                  # Haiku 5.5, ~5–8 phút
    .venv/bin/python scripts/live_claude.py --model claude-sonnet-5-5
    .venv/bin/python scripts/live_claude.py --graph          # thêm dựng sơ đồ bằng agent Opus (~5 phút nữa)

In ✓/✗ và thời gian từng mục, mã thoát 1 nếu có mục trượt."""
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

import app as A

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="claude-haiku-5-5", choices=list(A.CC.MODELS))
ap.add_argument("--graph", action="store_true", help="dựng sơ đồ bằng agent Claude (Opus)")
args = ap.parse_args()

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-live-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
A.store.ROOT = TMP / "docs"
A.KEYS_FILE, A._keys = TMP / "keys.json", {}
A.load_keys = lambda: None
A.USER_TERMS_FILE = TMP / "user_terms.json"      # như smoke_features: không bước nào được ghi vào data/ thật
A.PROVIDERS_FILE = TMP / "providers.json"
A.MODEL_META_FILE = TMP / "model_meta.json"
A.CC.WORK = TMP / "claude-work"                    # thư mục làm việc riêng: không lẫn lượt gọi của service đang chạy
B, M = A.CC.BASE, args.model
VI = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)
# đúng như giao diện gửi khi bật ✳️ Claude Code (static/app.js claudeCfg)
CFG = {"base_url": B, "model": M, "translator": "llm", "glossary_auto": False, "google_fallback": False,
       "chat_base_url": B, "chat_model": M, "gloss_base_url": B, "gloss_model": M, "align_base_url": B, "align_model": M,
       "see_base_url": B, "see_model": M, "ocr_vlm_base_url": B, "ocr_vlm_model": M, "formula_ocr": "ocr"}
FAIL = []


def ok(cond, msg, t0=None):
    took = f" ({time.time() - t0:.0f} s)" if t0 else ""
    print(("  ✓ " if cond else "  ✗ ") + msg + took, flush=True)
    if not cond:
        FAIL.append(msg)


def vi(text: str) -> bool:
    return len(VI.findall(text or "")) >= 3


print(f"Claude Code thật, model {M}")
with A.test_client() as c:
    s = c.get("/api/claude/status").json()
    ok(s["installed"] and s["logged_in"], f"đã cài, đã đăng nhập ({s.get('version')}, {s.get('detail')})")
    if not s["logged_in"]:
        sys.exit(1)
    with open(ROOT / "data/attention-is-all-you-need.pdf", "rb") as f:
        did = c.post("/api/upload", files={"file": ("attention.pdf", f, "application/pdf")}).json()["id"]
    D = A.DOCS[did]

    print("1. Bảng thuật ngữ")
    t = time.time()
    r = c.post(f"/api/{did}/glossary", json=CFG)
    terms = r.json().get("terms", []) if r.status_code == 200 else []
    viet = sum(bool(VI.search(x.get("vi", ""))) for x in terms)   # thuật ngữ ngắn ("mã hoá") chỉ một, hai chữ có dấu
    ok(len(terms) >= 10 and viet >= 5, f"lập {len(terms)} thuật ngữ, {viet} có cách dịch tiếng Việt (còn lại giữ nguyên như MQA)", t)

    print("2. Dịch")
    t = time.time()
    pg = c.post(f"/api/{did}/translate/0?span=1", json=CFG).json()["pages"][0]
    tr0 = D["trans"].get(0, {})
    ok(pg.get("translated", 0) >= pg.get("blocks", 99) - pg.get("skipped", 0) - 1 and sum(map(vi, tr0.values())) >= len(tr0) - 2,
       f"trang 1: dịch {pg.get('translated')}, bỏ qua {pg.get('skipped')} (tên tác giả) trên {pg.get('blocks')} đoạn, "
       f"tiếng Việt {sum(map(vi, tr0.values()))}/{len(tr0)} (tên bài giữ nguyên được)", t)
    t = time.time()
    r = c.post(f"/api/{did}/translate/2?span=2", json=CFG).json()
    pages = {p["page"]: p for p in r.get("pages", [])}
    ok(2 in D["done"] and 3 in D["done"] and not r.get("errors"), f"gộp trang 3–4 trong một lượt (nhóm {r.get('group')})", t)
    t = time.time()
    some = sorted(k for k, v in D["trans"][2].items() if len(v) > 40)[:2]
    for k in some:   # xoá bản dịch cũ: route gộp bản dịch mới vào bản cũ, không xoá thì mục này luôn đạt
        D["trans"][2][k] = "MỐC-CHƯA-DỊCH"
    r = c.post(f"/api/{did}/translate/2?blocks={','.join(map(str, some))}", json=CFG)
    new = [D["trans"][2].get(k, "") for k in some]
    ok(r.status_code == 200 and len(some) == 2 and all(vi(v) and "MỐC" not in v for v in new),
       f"dịch lại riêng hai đoạn của trang 3 (đoạn {some}: {new[0][:40]!r}…)", t)

    print("3. Trò chuyện (mọi nguồn ngữ cảnh)")
    sid = ""
    for mode, q in [("auto", "Bài này đề xuất kiến trúc gì? Một câu."),
                    ("pair", "Câu đầu của phần Introduction dịch có sát không? Một câu."),
                    ("source", "Tác giả nói gì về RNN ở trang đang xem? Một câu."),
                    ("translation", "Tóm tắt bản dịch trang này trong một câu."),
                    ("image", "Hình ở trang này vẽ gì? Một câu."),
                    ("full", "Kết luận của cả bài là gì? Một câu."),
                    ("pdf", "Bảng 2 so sánh gì? Một câu.")]:
        t = time.time()
        r = c.post(f"/api/{did}/chat", json={"cfg": CFG, "question": q, "page": 2, "context": mode, "session_id": sid})
        sid = r.headers.get("x-session-id", sid)
        ctx = json.loads(r.headers.get("x-context", "{}") or "{}")
        body = r.text
        # chế độ thật sự dùng: model ảnh lỗi thì app lặng lẽ chỉ gửi chữ, PDF lớn thì gửi chữ (X-Context nói đúng)
        right = (bool(ctx.get("images")) if mode == "image" else ctx.get("mode") == mode if mode != "auto" else True)
        ok(r.status_code == 200 and len(body.strip()) > 20 and "⚠️" not in body and vi(body) and right
           and ctx.get("model") == M,
           f"{mode}: {ctx.get('mode', '?')}, {ctx.get('model', '?')}{', ảnh ' + str(ctx.get('images')) if ctx.get('images') else ''}"
           f" — {body.strip()[:70]!r}", t)
    t = time.time()
    r = c.post(f"/api/{did}/chats/{sid}/summarize", json=CFG)
    ok(r.status_code == 200 and vi(json.dumps(r.json(), ensure_ascii=False)), "tổng hợp phiên thành ghi chú", t)

    print("4. Chép công thức thành LaTeX (ảnh công thức → Claude)")
    t = time.time()
    D.get("flatex", {}).pop(3, None)   # trò chuyện ở trên có thể đã chép trang này: xoá để lượt này gọi Claude thật
    r = c.post(f"/api/{did}/formulas", json={"cfg": CFG, "pages": [3]})
    j = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    ok(r.status_code == 200 and j.get("latex", 0) >= 3 and j.get("glyph") == 0 and not j.get("failed"), f"trang 4: {j}", t)

    print("5. Căn vệt tô (cụm tương ứng ở bản dịch)")
    t = time.time()
    src = "The Transformer allows for significantly more parallelization and can reach a new state of the art."
    dst = "Transformer cho phép song song hoá nhiều hơn đáng kể và có thể đạt kết quả tốt nhất mới."
    s0 = src.index("significantly more parallelization")
    r = c.post(f"/api/{did}/align", json={"cfg": CFG, "src": src, "s": s0, "e": s0 + len("significantly more parallelization"), "dst": dst})
    j = r.json()
    got = dst[j["s"]:j["e"]] if isinstance(j.get("s"), int) else ""
    ok(r.status_code == 200 and "song song" in got and len(got) < len(dst) * 0.7,   # một cụm, không phải cả câu
       f"“significantly more parallelization” → {got!r}", t)

    print("6. OCR trang scan bằng VLM (Claude đọc từng vùng)")
    scan = ROOT / "samples/attention-is-all-you-need-scan.pdf"
    if scan.exists():
        with open(scan, "rb") as f:
            sid2 = c.post("/api/upload", files={"file": ("scan.pdf", f, "application/pdf")}).json()["id"]
        t = time.time()
        r = c.post(f"/api/{sid2}/translate/0?span=1", json={**CFG, "ocr_engine": "vlm", "ocr_mode": "auto"}).json()
        p0 = r["pages"][0]
        texts = [b["text"] for b in A.DOCS[sid2]["blocks"][0][1]]
        ok(p0.get("ocr") and any("Attention" in x for x in texts) and p0.get("translated", 0) >= 3,
           f"OCR {len(texts)} khối, dịch {p0.get('translated')} đoạn", t)
    else:
        print("  - bỏ qua: không có samples/attention-is-all-you-need-scan.pdf (tạo bằng scripts/make_scans.py)")

    if args.graph:
        print("7. Dựng sơ đồ bằng agent Claude (Opus)")
        t = time.time()
        r = c.post(f"/api/{did}/graph/build", json={"cfg": CFG, "source": "claude"})
        while (st := c.get(f"/api/{did}/graph").json())["job"]["running"]:
            time.sleep(5)
        job = st["job"]
        ok(job["ok"] and st["graph"] and c.get(f"/api/{did}/graph/view").status_code == 200,
           f"sơ đồ {job.get('out')}: {(job.get('log') or [''])[-1][:80]}", t)

    left = [d.name for d in A.CC.WORK.iterdir() if not d.name.startswith(".")] if A.CC.WORK.exists() else []
    ok(not left, f"không còn thư mục làm việc tạm ({left[:3]})")

print("\n" + ("TẤT CẢ ĐẠT" if not FAIL else f"{len(FAIL)} MỤC TRƯỢT: " + "; ".join(FAIL)))
sys.exit(1 if FAIL else 0)
