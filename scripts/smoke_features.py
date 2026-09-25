"""Kiểm tra nhanh các tính năng đọc/dịch mà KHÔNG cần server LLM (thay hàm gọi LLM bằng hàm giả):
gộp trang, bảng thuật ngữ, lớp chữ + tô vàng + trò chuyện, nhớ API key, khôi phục sau khi tải lại trang,
lưu xuống đĩa rồi mở lại sau khi khởi động lại app (dùng thư mục tạm, không đụng data/docs/).

    .venv/bin/python scripts/smoke_features.py      # ~20 giây

In ✓/✗ cho từng mục; dùng sau khi sửa app.py để biết có làm hỏng đường nào không."""
import asyncio
import json
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
A.PROVIDERS_FILE = A.store.ROOT / "providers.json"      # …và nhà cung cấp người dùng đã lưu (data/providers.json)
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
    seen.update(system=system, msg=messages[-1]["content"], images=messages[-1].get("images"), model=cfg.model,
                n=len(messages))
    yield "Trả lời thử."
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
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download?sync=false").content)
    ok(not list(pdf[0].annots()), "tắt đồng bộ tô vàng: vệt tô ở bản gốc không vào bản dịch tải về")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download?which=original&sync=false").content)
    ok([a.type[1] for a in pdf[0].annots()] == ["Highlight"] and "BERT" in pdf[0].get_text(),
       "tải bản gốc có tô: đúng bản gốc, vệt tô ở bản gốc có trong đó")
    r = c.post(f"/api/{did}/translate/1?span=3&force=true", json=cfg).json()
    ok([p["page"] for p in r["pages"]] == [1, 2, 3], "dịch lại một dải trang (force): trang đã dịch cũng được gộp")
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [], "remove": ["m1"]})
    ok(c.get(f"/api/{did}/marks").json()["marks"] == {}, "bỏ tô vàng theo id")
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [mark]})
    c.post(f"/api/{did}/chat", json={"cfg":cfg,"messages":[{"role":"user","content":"Hỏi?"}],"page":0,
                                     "selection":"BERT is designed to","side":"original"})
    ok("Mục lục bài:" in seen["system"] and "bôi đen" in seen["msg"] and "[Trang 1 ·" in seen["msg"], "ngữ cảnh chat đủ (mục lục + đoạn chọn + trang)")

    print("3b. Căn vệt tô bằng LLM (LLM giả)")
    src = "We call our particular attention [[X]] (Figure 2)."
    dst = "Chúng tôi gọi cơ chế attention riêng này là “Scaled Dot-Product Attention” (Hình 2)."
    reply = {}
    async def fj(cfg, system, payload, schema, max_out=0, est_in=0, extra=None):
        reply["payload"] = payload
        return reply["out"]
    real_json, A.chat_json = A.chat_json, fj
    def ask(s, e, out):
        reply["out"] = out
        A._ALIGN_CACHE.clear()
        return c.post(f"/api/{did}/align", json={"cfg": cfg, "src": src, "s": s, "e": e, "dst": dst}).json()
    s0 = src.index("Figure 2")
    r = ask(s0, s0 + 8, {"match": "Hình 2"})
    ok(dst[r["s"]:r["e"]] == "Hình 2" and "[[Figure 2]]" in reply["payload"]["source"], "LLM trả đúng cụm → tô đúng cụm (Figure 2 → Hình 2)")
    r = ask(s0, s0 + 8, {"match": "hình  2"})
    ok(dst[r["s"]:r["e"]] == "Hình 2", "khác hoa/thường, thừa khoảng trắng vẫn tìm ra")
    r = ask(s0, s0 + 8, {"match": "Hình số 2"})
    ok(r["s"] is None and "không có trong câu" in r["reason"], "cụm không có thật trong câu bên kia → giữ tô cả câu")
    tries = iter([{"match": ""}, {"match": "Hình 2"}])
    async def fj2(*a, **k): return next(tries)
    A.chat_json = fj2
    A._ALIGN_CACHE.clear()
    r = c.post(f"/api/{did}/align", json={"cfg": cfg, "src": src, "s": s0, "e": s0 + 8, "dst": dst}).json()
    ok(r.get("match") == "Hình 2", "lần đầu trả rỗng → hỏi lại một lần")
    A.chat_json = fj
    r = ask(s0, s0 + 8, {"items": []})
    ok(r["s"] is None, "JSON rác → giữ tô cả câu")
    async def fbad(*a, **k): raise ValueError("LLM không trả về JSON")
    A.chat_json = fbad
    A._ALIGN_CACHE.clear()
    r = c.post(f"/api/{did}/align", json={"cfg": cfg, "src": src, "s": s0, "e": s0 + 8, "dst": dst})
    ok(r.status_code == 200 and r.json()["s"] is None, "model trả chữ không phải JSON → giữ tô cả câu (không lỗi 500)")
    A.chat_json = real_json

    print("4. API key")
    A.resolve_keys(A.Config(base_url="https://api.openai.com/v1", api_key="sk-proj-ABCDEFGH1234"))
    ok(c.get("/api/keys").json()["keys"] == {"https://api.openai.com/v1": "sk-p…1234"}, "chỉ trả key dạng che")
    ok(A.resolve_keys(A.Config(base_url="https://api.openai.com/v1")).api_key == "sk-proj-ABCDEFGH1234", "gửi rỗng → tự điền lại")
    c.request("DELETE", "/api/keys", params={"slot":"https://api.openai.com/v1"})
    k = A.resolve_keys(A.Config(base_url="https://openrouter.ai/api/v1", api_key="sk-or-SECRET123456",
                                align_base_url="http://192.168.1.10:10531/v1", chat_base_url="http://10.0.0.9/v1"))
    ok(A.align_cfg(k).api_key == "" and A.chat_cfg(k).api_key == "" and A.gloss_cfg(k).api_key == "sk-or-SECRET123456",
       "server riêng cho căn/trò chuyện không nhận key của server dịch (chỉ dùng chung khi để trống base URL)")
    c.request("DELETE", "/api/keys", params={"slot": "https://openrouter.ai/api/v1"})
    body = {"model": "m", "temperature": 0.2, "reasoning": {"enabled": False}}
    ok(A.refused_param(body, 400, '{"error":{"message":"the request exceeds the available context size"}}') is None
       and A.refused_param(body, 429, "rate limited temperature") is None,
       "lỗi vượt ngữ cảnh / 429 không làm mất temperature")
    ok(A.refused_param(body, 500, '{"error":{"message":"Bad Request"}}') == "temperature"
       and A.refused_param(body, 400, "Reasoning is mandatory for this endpoint") == "reasoning",
       "proxy từ chối temperature / model bắt buộc suy nghĩ → bỏ đúng tham số đó rồi hỏi lại")

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

    print("8. Ghi chú")
    g = c.post(f"/api/{did}/notes", json={"id": "general", "text": "Ý chính: pre-train hai chiều."}).json()
    g2 = c.post(f"/api/{did}/notes", json={"id": "general", "text": "Ý chính: **pre-train** hai chiều."}).json()
    ok(g["id"] == g2["id"] == "general" and len(c.get(f"/api/{did}/notes").json()["notes"]) == 1,
       "ghi chú chung: sửa lại vẫn là một ghi chú")
    mk = c.post(f"/api/{did}/notes", json={"text": "Câu này là định nghĩa MLM", "page": 0, "mark_id": "m1",
                                          "quote": "vài chữ"}).json()
    llm = c.post(f"/api/{did}/notes", json={"text": "$\\mathrm{softmax}$ là…", "page": 0, "source": "llm",
                                           "quote": "BERT is designed to", "meta": {"question": "Giải thích?", "model": "m"}}).json()
    ns = c.get(f"/api/{did}/notes").json()["notes"]
    ok([x["id"] for x in ns] == ["general", mk["id"], llm["id"]] and ns[2]["source"] == "llm",
       "thứ tự: ghi chú chung → theo trang; giữ nguồn LLM + câu hỏi")
    c.post(f"/api/{did}/chat", json={"cfg": cfg, "messages": [{"role": "user", "content": "Hỏi lại?"}], "page": 0,
                                     "note_ids": [mk["id"]]})
    ok("Câu này là định nghĩa MLM" in seen["msg"] and "Ý chính" not in seen["msg"],
       "💬 Hỏi lại: đúng ghi chú được chọn đi kèm câu hỏi")
    A.DOCS.clear()
    ok(c.get(f"/api/{did}/state").json()["notes"] == ns, "ghi chú còn nguyên sau khi khởi động lại app")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download").content)
    info = [(a.type[1], a.info["content"]) for a in pdf[0].annots()]
    ok(("Highlight", "Câu này là định nghĩa MLM") in info, "PDF tải về: ghi chú của vệt tô = nội dung popup của highlight")
    loose = [t for k, t in info if k == "Text"]
    ok(len(loose) == 1 and "pre-train" in loose[0] and "softmax" in loose[0], "ghi chú chung + ghi chú trang → biểu tượng ghi chú ở trang 1")
    md = c.get(f"/api/{did}/notes.md")
    ok(md.status_code == 200 and "## Ghi chú chung" in md.text and "## Trang 1" in md.text and "> vài chữ" in md.text
       and "Giải thích?" in md.text, "xuất .md: ghi chú chung, theo trang, kèm đoạn trích và câu hỏi LLM")
    c.post(f"/api/{did}/marks", json={"page": 0, "add": [], "remove": ["m1"]})
    ok(any(x["id"] == mk["id"] for x in c.get(f"/api/{did}/notes").json()["notes"]), "bỏ vệt tô không xoá ghi chú của nó")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download").content)
    ok(any(a.type[1] == "Text" and "Câu này là định nghĩa MLM" in a.info["content"] for a in pdf[0].annots()),
       "ghi chú của vệt đã bỏ vẫn có trong PDF (biểu tượng ghi chú)")
    ok(c.post(f"/api/{did}/notes", json={"id": "khongcon", "text": "x"}).status_code == 404,
       "sửa ghi chú đã bị xoá → 404, không tạo bản trùng")
    ok(c.delete(f"/api/{did}/notes/{llm['id']}").status_code == 200
       and c.delete(f"/api/{did}/notes/{llm['id']}").status_code == 404, "xoá ghi chú")
    ok(c.post(f"/api/{did}/notes", json={"text": "x", "page": 999}).json()["page"] is None, "số trang lạ → ghi chú cả bài")
    ok(c.get("/api/docs").json()["docs"][0]["notes"] == 3, "danh sách tài liệu đếm số ghi chú")

    d_live = A.DOCS[did]
    ok(c.delete(f"/api/docs/{did}").status_code == 200 and c.get(f"/api/{did}/state").status_code == 404,
       "xoá tài liệu → không mở lại được nữa")
    A.store.save_state(d_live)                      # một việc của bài (đang dịch / lập thuật ngữ) xong SAU khi bài bị xoá
    ok(not A.store.folder(did).exists(), "việc còn chạy của bài đã xoá không lỗi, không làm bài sống lại")

print("10. Phiên trò chuyện, chọn ngữ cảnh")
with TestClient(A.app) as c:
    with open(ROOT / "data/attention-is-all-you-need.pdf", "rb") as f:
        aid = c.post("/api/upload", files={"file": ("att.pdf", f, "application/pdf")}).json()["id"]
    c.post(f"/api/{aid}/translate/3?span=1", json=cfg)
    r = c.post(f"/api/{aid}/chat", json={"cfg": cfg, "question": "Giải thích đoạn này", "page": 3})
    sid, info = r.headers["x-session-id"], json.loads(r.headers["x-context"])
    ok(r.text == "Trả lời thử." and sid and info["pages"][0] == 4, f"phiên mới + ngữ cảnh bắt đầu từ trang đang xem ({info['pages'][:3]})")
    ok("⟦" in seen["msg"] and "{v1}" not in seen["msg"], "công thức trong ngữ cảnh là chữ của công thức, không phải {v1}")
    c.post(f"/api/{aid}/chat", json={"cfg": cfg, "question": "Còn gì nữa?", "session_id": sid, "page": 3})
    ok(seen["n"] == 3, "câu hỏi thứ hai: server tự ghép lịch sử phiên (3 tin nhắn)")
    S = c.get(f"/api/{aid}/chats/{sid}").json()
    ok([m["role"] for m in S["messages"]] == ["user", "assistant"] * 2 and S["messages"][1]["ctx"]["mode"] == "pair",
       "phiên lưu cả câu hỏi và câu trả lời (kèm ngữ cảnh đã gửi)")
    c.post(f"/api/{aid}/chat", json={"cfg": cfg, "session_id": sid, "regenerate": True})
    c.post(f"/api/{aid}/chat", json={"cfg": cfg, "session_id": sid, "edit": True, "question": "Hỏi khác", "page": 3})
    S = c.get(f"/api/{aid}/chats/{sid}").json()
    ok(len(S["messages"]) == 4 and S["messages"][2]["content"] == "Hỏi khác", "trả lời lại / sửa câu hỏi cuối: không nhân đôi tin nhắn")
    c.patch(f"/api/{aid}/chats/{sid}", json={"title": "Phiên thử"})
    A.DOCS.clear()                                    # như khởi động lại app
    L = c.get(f"/api/{aid}/chats").json()["sessions"]
    ok(L[0]["title"] == "Phiên thử" and L[0]["n"] == 2, "đổi tên; danh sách phiên còn sau khi khởi động lại")
    md = c.get(f"/api/{aid}/chats/{sid}.md").text
    ok("# Phiên thử" in md and "Hỏi khác" in md, "xuất phiên ra Markdown")
    n = c.post(f"/api/{aid}/chats/{sid}/summarize", json=cfg).json()
    ok(n["source"] == "llm" and n["meta"].get("session") == sid and "Phiên thử" in n["meta"]["question"],
       "tổng hợp phiên → ghi chú nguồn LLM")
    d = A.DOCS[aid]
    ask = lambda q, **k: A.ChatAsk(cfg=A.Config(**cfg), question=q, **k)
    P = A.chat_plan(d, ask("Tóm tắt đóng góp chính của bài", page=3), "Tóm tắt đóng góp chính của bài", 9000, False)
    ok(P["mode"] == "full" or P["pages"][:1] == [0], f"câu hỏi về cả bài → cả bài / phần mở đầu ({P['mode']})")
    P = A.chat_plan(d, ask("Hình 2 nói gì?", page=0), "Hình 2 nói gì?", 9000, True)
    ok(P["pages"][0] == 3 and P["images"] == [3], f"“Hình 2” → trang có chú thích Figure 2 (tr.4), gửi ảnh: {P['why']}")
    P = A.chat_plan(d, ask("Hình 2 nói gì?", page=0), "Hình 2 nói gì?", 9000, False)
    ok(not P["images"], "chưa có model xem ảnh → không gửi ảnh")
    P = A.chat_plan(d, ask("multi-head attention là gì", page=0), "multi-head attention là gì", 9000, False)
    ok(3 in P["pages"] or 4 in P["pages"], f"trang liên quan theo từ khoá: {[p + 1 for p in P['pages']]}")
    P = A.chat_plan(d, ask("trang 7 nói gì", page=0), "trang 7 nói gì", 9000, False)
    ok(P["pages"][0] == 6, "“trang 7” → trang 7 trước")
    P = A.chat_plan(d, ask("x", page=0, context="source"), "x", 9000, False)
    ok(P["mode"] == "source", "chọn tay chế độ ngữ cảnh")
    A.MODEL_META["https://see/v1"] = {}
    ok(A.see_cfg(A.Config(**cfg)) is None and A.see_cfg(A.Config(**cfg, see_base_url="https://see/v1", see_model="v")).model == "v",
       "model xem ảnh: chưa cấu hình → không có")
    c2 = {**cfg, "see_base_url": "https://see/v1", "see_model": "eye"}
    r = c.post(f"/api/{aid}/chat", json={"cfg": c2, "question": "Hình 2 vẽ gì?", "page": 0})
    ok(seen["model"] == "eye" and seen["images"] and len(seen["images"]) == 1 and json.loads(r.headers["x-context"])["images"] == [4],
       "hỏi về hình → model xem ảnh nhận ảnh trang 4")

print("9. Nhà cung cấp, thông tin model")
with TestClient(A.app) as c:
    ps = c.get("/api/providers").json()["providers"]
    ok({"openrouter", "tuf", "chatgpt"} <= {p["id"] for p in ps} and not next(p for p in ps if p["id"] == "tuf")["builtin"],
       "lần đầu: có sẵn + gieo TUF/ChatGPT thành nhà cung cấp của người dùng")
    r = c.post("/api/providers", json={"name": "Máy Lab", "base_url": "http://lab:8000/v1/", "model": "m"}).json()
    ok(r["id"] == "may-lab" and r["base_url"] == "http://lab:8000/v1", "thêm mới: id theo tên (bỏ dấu), bỏ / cuối URL")
    r2 = c.post("/api/providers", json={"id": "may-lab", "name": "Máy Lab", "base_url": "http://lab:9000/v1"}).json()
    ok(r2["id"] == "may-lab" and sum(p["id"] == "may-lab" for p in c.get("/api/providers").json()["providers"]) == 1,
       "sửa theo id: không tạo bản trùng")
    ok(c.post("/api/providers", json={"id": "openrouter", "name": "x", "base_url": "http://x"}).status_code == 400,
       "không đè được nhà cung cấp có sẵn")
    ok(c.post("/api/providers", json={"name": "Máy Lab", "base_url": "http://y"}).json()["id"] == "may-lab-2",
       "trùng tên → id khác")
    c.delete("/api/providers/may-lab")
    ok("may-lab" not in {p["id"] for p in c.get("/api/providers").json()["providers"]}, "xoá")
    ok(c.delete("/api/providers/openai").status_code == 404, "không xoá được nhà cung cấp có sẵn")
m = A.model_meta({"id": "a/b:free", "context_length": 262144, "architecture": {"input_modalities": ["text", "image"]},
                  "pricing": {"prompt": "0", "completion": "0"}})
m2 = A.model_meta({"id": "local"})
ok(m == {"id": "a/b:free", "name": "", "ctx": 262144, "vision": True, "free": True} and m2["vision"] is None and m2["free"] is None,
   "thông tin model: OpenRouter có ctx/ảnh/free, server khác để trống (không đoán)")

print("\nTẤT CẢ ĐỀU ĐẠT" if not FAIL else f"\n{len(FAIL)} MỤC SAI: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
