"""Kiểm phần không dịch mặc định (tên tác giả đầu bài, tài liệu tham khảo) và nút dịch lại đoạn đang bỏ qua, KHÔNG cần
server LLM (hàm gọi LLM được thay bằng hàm giả, thư mục lưu tạm, không đụng data/docs/).

    .venv/bin/python scripts/test_skip.py      # ~1 phút (có OCR một trang scan)

In ✓/✗ cho từng mục, mã thoát 1 nếu có mục trượt."""
import atexit
import pathlib
import re
import shutil
import sys
import tempfile

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent
A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-skip-"))
atexit.register(shutil.rmtree, A.store.ROOT, ignore_errors=True)
A.KEYS_FILE, A._keys = A.store.ROOT / "keys.json", {}
A.USER_TERMS_FILE = A.store.ROOT / "user_terms.json"
A.PROVIDERS_FILE = A.store.ROOT / "providers.json"
A.MODEL_META_FILE = A.store.ROOT / "model_meta.json"
A._uterms.clear()
A._uterms.update(blocked={}, prefer={})
A.load_keys = lambda: None

SENT = []   # mỗi lượt gọi LLM: số hiệu các mục gửi đi (đánh lại từ 0 trong mỗi lượt, không phải số hiệu đoạn)


async def fc(cfg, system, payload, max_out=0, est_in=0):
    SENT.append([it["id"] for it in payload["items"]])
    return {it["id"]: "DỊCH " + re.sub(r"\b[A-Za-z]{2,}\b", r"\g<0>ă", it["text"]) for it in payload["items"]}
A.chat = fc


async def fi(b, m=""): return ("other", 32768, 4)
A.server_info = fi
FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


print("1. Nhận ra khối tên tác giả (lớp chữ, trang đầu)")
first = {f.stem: A.extract_blocks(pymupdf.open(f)[0]) for f in sorted((ROOT / "data").glob("*.pdf"))}
names = {k: A.author_blocks(0, bl) for k, bl in first.items()}
ok(names["attention-is-all-you-need"] == set(range(2, 10)), "Attention: 8 khối tác giả giữa tên bài và Abstract")
ok(names["bert"] == {1} and names["mixtral"] == {1}, "BERT, Mixtral (danh sách tên 448 ký tự): khối tác giả")
ok(names["datasentinel"] == {1, 2, 3}, "DataSentinel: tên, nơi làm việc, email")
ok(names["mistral-7b"] == set(), "Mistral 7B không có khối tên bài (doc_title) → không đoán, dịch cả")
ok(all(A.author_blocks(1, bl) == set() for bl in first.values()), "chỉ trang đầu")
bl = first["bert"]
prose = [dict(bl[1], text="We study a new method for training language models and show that it improves results. " * 6)]
ok(A.author_blocks(0, bl[:1] + prose + bl[2:]) == set(), "văn xuôi dài sau tên bài → không đoán")
ok(A.author_blocks(0, bl[:2]) == set(), "dãy khối không dừng ở Abstract hay tiêu đề mục → không đoán")
ok(not A.author_blocks(0, [b for b in bl if b.get("label") != "doc_title"]), "không có tên bài → không đoán")

cfg = {"base_url": "http://x/v1", "model": "m", "glossary_auto": False, "translator": "llm"}
with A.test_client() as c:
    print("2. Dịch trang đầu: khối tác giả giữ chữ gốc, tắt được trong cấu hình")
    with open(ROOT / "data/bert.pdf", "rb") as f:
        bid = c.post("/api/upload", files={"file": ("b.pdf", f, "application/pdf")}).json()["id"]
    SENT.clear()
    r = c.post(f"/api/{bid}/translate/0?span=1", json=cfg).json()["pages"][0]
    D = A.DOCS[bid]
    ok(1 not in D["trans"][0] and sum(map(len, SENT)) == r["blocks"] - 1 and r["skipped"] == 1,
       "khối tác giả BERT không gửi LLM, đếm vào đoạn giữ nguyên")
    L = c.get(f"/api/{bid}/page/0/layer?kind=original").json()
    ok(L["authors"] == [1] and L["skip"] == [] and L["refs"] == [], "lớp chữ báo đoạn 1 là tên tác giả")
    L = c.get(f"/api/{bid}/page/0/layer?kind=original&authors=false").json()
    ok(L["authors"] == [], "ô cấu hình tắt → lớp chữ không đánh dấu")
    SENT.clear()
    c.post(f"/api/{bid}/translate/0?span=1&force=true", json={**cfg, "skip_authors": False})
    ok(1 in D["trans"][0], "tắt “không dịch tên tác giả” → dịch cả khối tác giả")

    print("3. ↩ Dịch đoạn đang bỏ qua: tên tác giả, tài liệu tham khảo, ⛔")
    copy = pymupdf.open(ROOT / "data/bert.pdf")   # cùng file thì app mở lại bản cũ (so SHA-256): dùng bản khác byte
    copy.set_metadata({**copy.metadata, "subject": "test_skip"})
    b2 = c.post("/api/upload", files={"file": ("b2.pdf", copy.tobytes(), "application/pdf")}).json()["id"]
    ok(b2 != bid, "bản sao khác byte là tài liệu mới")
    D2 = A.DOCS[b2]
    c.post(f"/api/{b2}/translate/0?span=1", json=cfg)
    SENT.clear()
    r = c.post(f"/api/{b2}/page/0/skip", json={"cfg": cfg, "blocks": [1], "skip": False}).json()
    ok(r["force"] == [1] and 1 not in r["skip"] and r["rendered"] and list(map(len, SENT)) == [1] and 1 in D2["trans"][0],
       "bảo dịch khối tác giả trên trang đã dịch → dịch riêng đúng đoạn đó")
    L = c.get(f"/api/{b2}/page/0/layer?kind=original").json()
    ok(L["authors"] == [], "đoạn đã bảo dịch không còn hiện là bỏ qua")
    SENT.clear()
    c.post(f"/api/{b2}/translate/0?span=1&force=true", json=cfg)
    ok(sum(map(len, SENT)) == len(D2["blocks"][0][1]), "dịch lại cả trang vẫn nhớ: mọi đoạn, kể cả khối tác giả, được gửi LLM")
    r = c.post(f"/api/{b2}/page/0/skip", json={"cfg": cfg, "blocks": [1], "skip": True}).json()
    ok(r["user"] == [1] and r["force"] == [], "⛔ lại đoạn đó → thành đoạn bạn chọn không dịch, bỏ dấu bảo dịch")
    r = c.post(f"/api/{b2}/page/0/skip", json={"cfg": cfg, "blocks": [1, 4], "skip": True}).json()
    SENT.clear()
    r = c.post(f"/api/{b2}/page/0/skip", json={"cfg": cfg, "blocks": [1, 4], "skip": False}).json()
    ok(r["skip"] == [] and r["user"] == [] and 1 in D2["trans"][0] and 4 in D2["trans"][0],
       "nút “↩ Dịch phần bỏ qua”: gửi mọi đoạn đang bỏ qua (⛔ và tên tác giả) → không còn đoạn nào bỏ qua")
    c.post(f"/api/{b2}/translate/10?span=1", json=cfg)
    refs = c.get(f"/api/{b2}/page/10/layer?kind=original").json()["refs"]
    SENT.clear()
    r = c.post(f"/api/{b2}/page/10/skip", json={"cfg": cfg, "blocks": refs[:2], "skip": False}).json()
    ok(len(refs) > 10 and r["force"] == refs[:2] and list(map(len, SENT)) == [2] and all(i in D2["trans"][10] for i in refs[:2])
       and not any(i in D2["trans"][10] for i in refs[2:]),
       "bảo dịch hai mục tài liệu tham khảo → chỉ hai mục đó được dịch")

    print("4. Lưu xuống đĩa: dấu bảo dịch còn sau khi khởi động lại")
    A.store.save_state(D2)
    A.DOCS.pop(b2)
    L = c.get(f"/api/{b2}/page/0/layer?kind=original").json()
    ok(L["authors"] == [] and A.DOCS[b2]["force"].get(10) and sorted(A.DOCS[b2]["force"][10]) == refs[:2],
       "nạp lại tài liệu: dấu bảo dịch (force) của trang 1 và 11 còn nguyên")

    print("5. Bản scan (OCR): khối tác giả cũng giữ chữ gốc")
    with open(ROOT / "samples/gqa-scan.pdf", "rb") as f:
        sid = c.post("/api/upload", files={"file": ("g.pdf", f, "application/pdf")}).json()["id"]
    SENT.clear()
    c.post(f"/api/{sid}/translate/0?span=1", json={**cfg, "ocr_mode": "auto"})
    S = A.DOCS[sid]
    blocks = S["blocks"][0][1]
    got = A.author_blocks(0, blocks)
    ok(got and all(i not in S["trans"][0] for i in got)
       and any("Ainslie" in A.untag(blocks[i]["text"]) for i in got), f"gqa-scan: khối tác giả {sorted(got)} không dịch")

print("\nTẤT CẢ ĐỀU ĐẠT" if not FAIL else f"\n{len(FAIL)} mục trượt")
sys.exit(1 if FAIL else 0)
