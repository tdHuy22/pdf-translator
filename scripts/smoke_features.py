"""Kiểm tra nhanh các tính năng đọc/dịch mà KHÔNG cần server LLM (thay hàm gọi LLM bằng hàm giả):
gộp trang, bảng thuật ngữ, lớp chữ + tô vàng + trò chuyện, nhớ API key, khôi phục sau khi tải lại trang,
lưu xuống đĩa rồi mở lại sau khi khởi động lại app (dùng thư mục tạm, không đụng data/docs/).

    .venv/bin/python scripts/smoke_features.py      # ~20 giây

In ✓/✗ cho từng mục; dùng sau khi sửa app.py để biết có làm hỏng đường nào không."""
import asyncio
import pathlib
import sys
import tempfile

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent

A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-smoke-"))
A.KEYS_FILE, A._keys = A.store.ROOT / "keys.json", {}   # không đụng key thật trong data/keys.json
A.USER_TERMS_FILE = A.store.ROOT / "user_terms.json"    # …và thuật ngữ người dùng đã dạy (data/user_terms.json)
A._uterms.clear()
A._uterms.update(blocked={}, prefer={})
A.load_keys = lambda: None

CALLS = []

async def fc(cfg, system, payload, max_out=0, est_in=0):
    CALLS.append(len(payload["items"]))
    if "paper" in payload:
        return {it["id"]: (it["text"] if i % 3 == 0 else f"nghĩa{i}") for i, it in enumerate(payload["items"])}
    return {it["id"]: "DỊCH " + it["text"] for it in payload["items"]}
A.chat = fc
async def fj(cfg, system, payload, schema, max_out=0, est_in=0, extra=None):   # bảng thuật ngữ: xếp loại + dịch
    CALLS.append(len(payload["items"]))
    return {"items": [{"id": it["id"], "kind": "name" if i % 3 == 0 else "term", "vi": f"nghĩa{i}"}
                      for i, it in enumerate(payload["items"])]}
A.chat_json = fj
async def fi(b, m=""): return ("other", 32768, 4)
A.server_info = fi
seen = {}
async def fs(cfg, system, messages, num_ctx=0):
    seen.update(system=system, msg=messages[-1]["content"]); yield "Trả lời thử."
A.chat_stream = fs
FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ SAI ") + msg)
    if not cond:
        FAIL.append(msg)

with TestClient(A.app) as c:
    with open(pathlib.Path(__file__).resolve().parent.parent / "data/bert.pdf", "rb") as f:
        did = c.post("/api/upload", files={"file":("bert.pdf",f,"application/pdf")}).json()["id"]
    cfg = {"translator":"llm","base_url":"http://x/v1","model":"m","ocr_mode":"off","google_fallback":False}

    print("1. Gộp trang")
    CALLS.clear()
    r = c.post(f"/api/{did}/translate/0?span=3", json=cfg).json()
    ok(r["group"] == 3 and [p["page"] for p in r["pages"]] == [0,1,2], f"span=3 → gộp {r['group']} trang trong {len(CALLS)} lượt gọi ({CALLS[1] if len(CALLS)>1 else CALLS[0]} đoạn)")
    r1 = c.post(f"/api/{did}/translate/3?span=1", json=cfg).json()
    ok(r1["group"] == 1, "span=1 → không gộp")

    print("2. Bảng thuật ngữ")
    g = c.post(f"/api/{did}/glossary", json=cfg).json()
    want = A.GLOSSARY_ROWS
    ok(len(g["terms"]) == want, f"{len(g['terms'])} thuật ngữ, {sum(t['keep'] for t in g['terms'])} giữ nguyên")
    n_used = sum(t["use"] for t in g["terms"])
    t = g["terms"]
    tr = [x for x in t if not x["keep"]]                 # hàng "dịch": một hàng chuyển sang giữ nguyên, một hàng sửa chữ
    tr[0]["keep"] = True; tr[1]["vi"] = "SỬA TAY"; next(x for x in t if x not in tr[:2])["use"] = False
    kept_t, edited_t = tr[0]["term"], tr[1]["term"]
    used = c.put(f"/api/{did}/glossary", json={"terms": t}).json()["used"]
    ok(used == n_used - 1, f"bỏ 1 hàng → còn {used} thuật ngữ vào prompt")
    c.post(f"/api/{did}/translate/4?span=1", json=cfg)
    gl = A.glossary_text(A.Config(**cfg), A.DOCS[did])
    ok(f"{kept_t} = {kept_t}" in gl and "SỬA TAY" in gl, "bảng đã sửa vào đúng prompt")

    print("3. Lớp chữ / tô vàng / trò chuyện")
    L = c.get(f"/api/{did}/page/0/layer").json()
    T = c.get(f"/api/{did}/page/0/layer?kind=translated").json()
    ok(L["lines"] and T["lines"] and set(L["blocks"]) == set(T["blocks"]), f"khối gốc {len(L['blocks'])} ≡ khối dịch {len(T['blocks'])}")
    mark = {"id": "m1", "block": 2, "side": "original", "s": 5, "e": 40, "text": "vài chữ",
            "rects": [[70, 250, 200, 262]], "peer": [[70, 250, 200, 262], [70, 262, 150, 274]],
            "ps": 0, "pe": 62}   # câu tương ứng bên bản dịch, để biết vệt nào đang bị bôi đè
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [mark]})
    got = c.get(f"/api/{did}/marks").json()["marks"]["0"]
    ok(len(got) == 1 and (got[0]["s"], got[0]["e"]) == (5, 40) and (got[0]["ps"], got[0]["pe"]) == (0, 62),
       "vệt tô lưu theo vị trí ký tự ở CẢ HAI bản, không phải cả đoạn")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download").content)
    ok([a.type[1] for a in pdf[0].annots()] == ["Highlight"] * 2, "PDF tải về: mỗi khung chữ một annotation highlight")
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [], "remove": ["m1"]})
    ok(c.get(f"/api/{did}/marks").json()["marks"] == {}, "bỏ tô vàng theo id")
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [mark]})
    c.post(f"/api/{did}/chat", json={"cfg":cfg,"messages":[{"role":"user","content":"Hỏi?"}],"page":0,
                                     "selection":"BERT is designed to","side":"original"})
    ok("Mục lục bài:" in seen["system"] and "bôi đen" in seen["msg"] and "[Trang 1]" in seen["msg"], "ngữ cảnh chat đủ (mục lục + đoạn chọn + trang)")

    print("4. API key")
    A.resolve_keys(A.Config(base_url="https://api.openai.com/v1", api_key="sk-proj-ABCDEFGH1234"))
    ok(c.get("/api/keys").json()["keys"] == {"https://api.openai.com/v1": "sk-p…1234"}, "chỉ trả key dạng che")
    ok(A.resolve_keys(A.Config(base_url="https://api.openai.com/v1")).api_key == "sk-proj-ABCDEFGH1234", "gửi rỗng → tự điền lại")
    c.request("DELETE", "/api/keys", params={"slot":"https://api.openai.com/v1"})

    print("5. Khôi phục sau khi tải lại trang")
    st = c.get(f"/api/{did}/state").json()
    ok(st["done"] == [0,1,2,3,4] and len(st["marks"].get("0", [])) == 1 and len(st["terms"]) == want,
       f"state: {len(st['done'])} trang đã dịch, tô vàng + bảng thuật ngữ còn nguyên")

    print("6. Lưu xuống đĩa, mở lại sau khi khởi động lại app")
    before = c.get(f"/api/{did}/page/2/translated.png").content
    L0 = c.get(f"/api/{did}/page/0/layer").json()
    A.DOCS.clear()                                  # như vừa khởi động lại: bộ nhớ trống, chỉ còn đĩa
    st2 = c.get(f"/api/{did}/state").json()
    ok(st2 == st, "state đọc từ đĩa giống hệt trước khi khởi động lại")
    ok(c.get(f"/api/{did}/page/2/translated.png").content == before, "ảnh trang đã dịch dựng lại từ out.pdf giống hệt")
    ok(c.get(f"/api/{did}/page/0/layer").json()["blocks"] == L0["blocks"], "số hiệu đoạn giữ nguyên (tô vàng vẫn khớp)")
    ok(A.glossary_text(A.Config(**cfg), A.DOCS[did]) == gl, "bảng thuật ngữ đã sửa vẫn vào prompt")
    c.post(f"/api/{did}/chat", json={"cfg":cfg,"messages":[{"role":"user","content":"Hỏi?"}],"page":0})
    ok("DỊCH " in seen["msg"], "trò chuyện sau khi nạp lại vẫn có bản dịch của trang trong ngữ cảnh")
    CALLS.clear()
    r = c.post(f"/api/{did}/translate/1?span=1", json=cfg).json()
    ok(r["pages"][0].get("cached") and not CALLS, "trang đã dịch không gọi LLM lại")
    c.post(f"/api/{did}/translate/5?span=1", json=cfg)
    ok(5 in A.DOCS[did]["done"], "dịch tiếp trang mới trên tài liệu vừa nạp lại")
    with open(pathlib.Path(__file__).resolve().parent.parent / "data/bert.pdf", "rb") as f:
        up = c.post("/api/upload", files={"file": ("bert-copy.pdf", f, "application/pdf")}).json()
    ok(up["id"] == did and up.get("existing"), "tải lên lại đúng file → mở lại tài liệu cũ, không tạo bản mới")
    docs = c.get("/api/docs").json()["docs"]
    ok(len(docs) == 1 and docs[0]["done"] == 6 and docs[0]["marks"] == 1, f"danh sách tài liệu: {docs[0]['done']} trang đã dịch")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download").content)
    ok([a.type[1] for a in pdf[0].annots()] == ["Highlight"] * 2, "PDF tải về sau khi nạp lại vẫn có tô vàng")
    ok(c.get("/api/..%2F..%2Fapp/state").status_code == 404, "id lạ không đọc ra ngoài thư mục lưu")

    print("7. Học từ bảng thuật ngữ người dùng lưu")
    t = c.get(f"/api/{did}/state").json()["terms"]
    edited = edited_t                                  # đã sửa cách dịch thành "SỬA TAY" ở mục 2
    gone = next(x["term"] for x in t if x["term"] not in (edited, kept_t))
    key = lambda term: A._term_key(term.split())
    ok(key(edited) in A.user_terms()["prefer"], f"sửa cách dịch '{edited}' → nhớ cho tài liệu sau")
    r = c.put(f"/api/{did}/glossary", json={"terms": [x for x in t if x["term"] != gone]}).json()
    ok(key(gone) in A.user_terms()["blocked"], f"xoá '{gone}' → nhớ để không gợi ý lại")
    made = asyncio.run(A.build_glossary(A.Config(**cfg), pymupdf.open(ROOT / "data/bert.pdf")))
    rows = {x["term"]: x for x in made["rows"]}
    ok(gone not in rows and any(x["term"] == gone and x["hidden"] for x in made["extra"]),
       "lập bảng lần sau: thuật ngữ đã xoá bị ẩn, vẫn nằm trong 'đang ẩn' để khôi phục")
    ok(rows.get(edited, {}).get("vi") == "SỬA TAY", "lập bảng lần sau: dùng cách dịch đã sửa")
    c.delete("/api/user_terms/blocked", params={"term": gone})
    ok(key(gone) not in A.user_terms()["blocked"], "bấm ↺ → bỏ ẩn")

    ok(c.delete(f"/api/docs/{did}").status_code == 200 and c.get(f"/api/{did}/state").status_code == 404,
       "xoá tài liệu → không mở lại được nữa")

print("\nTẤT CẢ ĐỀU ĐẠT" if not FAIL else f"\n{len(FAIL)} MỤC SAI: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
