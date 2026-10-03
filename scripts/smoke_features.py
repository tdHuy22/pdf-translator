"""Kiểm tra nhanh các tính năng đọc/dịch mà KHÔNG cần server LLM (thay hàm gọi LLM bằng hàm giả):
gộp trang, bảng thuật ngữ, lớp chữ + tô vàng + trò chuyện, nhớ API key, khôi phục sau khi tải lại trang,
lưu xuống đĩa rồi mở lại sau khi khởi động lại app (dùng thư mục tạm, không đụng data/docs/).

    .venv/bin/python scripts/smoke_features.py      # ~20 giây

In ✓/✗ cho từng mục; dùng sau khi sửa app.py để biết có làm hỏng đường nào không."""
import asyncio
import atexit
import contextlib
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent

A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-smoke-"))
atexit.register(shutil.rmtree, A.store.ROOT, ignore_errors=True)   # xoá khi thoát, không để sót thư mục tạm
A.KEYS_FILE, A._keys = A.store.ROOT / "keys.json", {}   # không đụng key thật trong data/keys.json
A.USER_TERMS_FILE = A.store.ROOT / "user_terms.json"    # …và thuật ngữ người dùng đã dạy (data/user_terms.json)
A.PROVIDERS_FILE = A.store.ROOT / "providers.json"      # …và nhà cung cấp người dùng đã lưu (data/providers.json)
A.MODEL_META_FILE = A.store.ROOT / "model_meta.json"    # …và nhãn model đã tải (data/model_meta.json)
A._uterms.clear()
A._uterms.update(blocked={}, prefer={})
A.load_keys = lambda: None

CALLS = []

async def fc(cfg, system, payload, max_out=0, est_in=0):
    CALLS.append(len(payload["items"]))
    if "paper" in payload:
        return {it["id"]: (it["text"] if i % 3 == 0 else f"nghĩa{i}") for i, it in enumerate(payload["items"])}
    # "bản dịch" giả không được là chữ gốc nguyên văn: app loại bản dịch còn nguyên cụm tiếng Anh (copied_run)
    return {it["id"]: "DỊCH " + re.sub(r"\b[A-Za-z]{2,}\b", r"\g<0>ă", it["text"]) for it in payload["items"]}
A.chat = fc
async def fj(cfg, system, payload, schema, max_out=0, est_in=0, extra=None):   # bảng thuật ngữ: xếp loại + dịch
    CALLS.append(len(payload["items"]))
    return {"items": [{"id": it["id"], "kind": "name" if i % 3 == 0 else "term", "vi": f"nghĩa{i}"}
                      for i, it in enumerate(payload["items"])]}
A.chat_json = fj
async def fi(b, m=""): return ("other", 32768, 4)
A.server_info = fi
seen = {}
REAL_STREAM = A.chat_stream
async def fs(cfg, system, messages, num_ctx=0):
    seen.update(system=system, msg=messages[-1]["content"], images=messages[-1].get("images"), model=cfg.model,
                n=len(messages), pdf=messages[-1].get("pdf"))
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
    cfg = {"translator":"llm","base_url":"http://x/v1","model":"m","ocr_mode":"off","google_fallback":False,
           "formula_ocr":"off"}   # nhận dạng công thức gọi Ollama thật → chỉ bật ở mục 14 (server giả)

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
    org = pymupdf.open("pdf", c.get(f"/api/{did}/download?which=original").content)
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download?which=dual&layout=side").content)
    ok(pdf.page_count == org.page_count and abs(pdf[0].rect.width - 2 * org[0].rect.width) < 1
       and len(list(pdf[0].annots())) == 3 and "BERT" in pdf[0].get_text() and "DỊCH" in pdf[0].get_text(),
       "PDF song ngữ cạnh nhau: gấp đôi bề rộng, đủ hai bản, vệt tô của cả hai bên")
    pdf = pymupdf.open("pdf", c.get(f"/api/{did}/download?which=dual&layout=alt").content)
    ok(pdf.page_count == 2 * org.page_count and "DỊCH" not in pdf[0].get_text() and "DỊCH" in pdf[1].get_text()
       and (not org.get_toc() or pdf.get_toc()[0][2] == 2 * org.get_toc()[0][2] - 1),
       "PDF song ngữ xen kẽ: trang gốc rồi trang dịch, mục lục trỏ đúng trang gốc")
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
    b2 = dict(body)
    ok(A.relax(b2, "reasoning") == "reasoning:min" and b2["reasoning"] == A.REASONING_MIN
       and A.relax(b2, "reasoning") == "reasoning" and "reasoning" not in b2 and b2["temperature"] == 0.2,
       "model bắt buộc suy nghĩ: lùi về mức suy nghĩ thấp nhất, vẫn bị từ chối mới bỏ hẳn")
    OR_URL = "https://openrouter.ai/api/v1/chat/completions"
    A._REFUSED[(OR_URL, "bunny")] = {"reasoning:min"}
    nb = A.no_think(A.Config(model="bunny"), OR_URL, {"model": "bunny"})
    A._REFUSED[(OR_URL, "bunny")] = {"reasoning:min", "reasoning"}
    nb2 = A.no_think(A.Config(model="bunny"), OR_URL, {"model": "bunny"})
    A._REFUSED.pop((OR_URL, "bunny"))
    ok(nb.get("reasoning") == A.REASONING_MIN and "reasoning" not in nb2,
       "đã nhớ model bắt buộc suy nghĩ: lần sau xin luôn mức thấp nhất (mức đó cũng bị từ chối thì bỏ hẳn)")

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

print("11. Không dịch đoạn/trang, dịch lại kèm yêu cầu")
SYS = []
_fc = A.chat
async def fc2(cfg_, system, payload, max_out=0, est_in=0):
    SYS.append((system, [it["id"] for it in payload["items"]]))
    return await _fc(cfg_, system, payload, max_out, est_in)
A.chat = fc2
with TestClient(A.app) as c:
    with open(ROOT / "data/bert.pdf", "rb") as f:
        bid = c.post("/api/upload", files={"file": ("b.pdf", f, "application/pdf")}).json()["id"]
    c1 = {**cfg, "glossary_auto": False}
    r = c.post(f"/api/{bid}/translate/10?span=1", json=c1).json()["pages"][0]
    ok(r["skipped"] > 10 and r["translated"] < r["blocks"] - 10, f"trang tài liệu tham khảo: {r['skipped']}/{r['blocks']} đoạn giữ nguyên")
    r = c.post(f"/api/{bid}/translate/10?span=1&force=true", json={**c1, "skip_refs": False}).json()["pages"][0]
    ok(r["skipped"] == 0, "tắt “không dịch tài liệu tham khảo” → dịch cả")
    c.post(f"/api/{bid}/translate/0?span=1", json=c1)
    D = A.DOCS[bid]
    before = dict(D["trans"][0])
    SYS.clear()
    r = c.post(f"/api/{bid}/page/0/skip", json={"cfg": c1, "blocks": [4, 5], "skip": True}).json()
    ok(r["skip"] == [4, 5] and r["rendered"] and not SYS, "⛔ hai đoạn trên trang đã dịch → dựng lại ngay, không gọi LLM")
    L = c.get(f"/api/{bid}/page/0/layer?kind=translated").json()
    txt = " ".join(l["text"] for l in L["lines"] if l["b"] == 4)
    ok(txt and "DỊCH" not in txt and "4" in L["blocks"], "đoạn không dịch: bản dịch giữ chữ gốc, lớp chữ vẫn có khung đoạn đó")
    SYS.clear()
    c.post(f"/api/{bid}/translate/0?span=1&force=true", json=c1)
    n0 = len(D["blocks"][0][1])
    ok(SYS and sum(len(x[1]) for x in SYS) <= n0 - 2 and 4 not in D["trans"][0] and 5 not in D["trans"][0],
       "dịch lại trang: đoạn ⛔ không gửi LLM")
    SYS.clear()
    r = c.post(f"/api/{bid}/page/0/skip", json={"cfg": c1, "blocks": [4], "skip": False}).json()
    ok(r["skip"] == [5] and SYS and len(SYS[0][1]) == 1 and 4 in D["trans"][0], "bỏ ⛔ đoạn chưa từng dịch → dịch riêng đoạn đó")
    SYS.clear()
    c.post(f"/api/{bid}/translate/0?blocks=2,3", json={**c1, "instruction": "Dịch sát nghĩa hơn"})
    ok(SYS and len(SYS[0][1]) == 2 and "Dịch sát nghĩa hơn" in SYS[0][0] and SYS[0][0].index("Glossary") < SYS[0][0].index("Dịch sát")
       if "Glossary" in SYS[0][0] else "Dịch sát nghĩa hơn" in SYS[0][0].split("\n")[-1],
       "dịch lại hai đoạn kèm yêu cầu: chỉ hai đoạn đó, yêu cầu ở cuối system prompt")
    ok(len(D["trans"][0]) >= len(before) - 2, "dịch lại một phần giữ bản dịch các đoạn khác")
    SYS.clear()
    c.post(f"/api/{bid}/translate/1?span=1&force=true", json={**c1, "instruction": "Giữ thuật ngữ tiếng Anh", "instruction_scope": "doc"})
    c.post(f"/api/{bid}/translate/2?span=1", json=c1)
    ok(all("Giữ thuật ngữ tiếng Anh" in x[0] for x in SYS), "yêu cầu “áp dụng cả bài” được nhớ cho trang sau")
    r = c.post(f"/api/{bid}/page/3/keep", json={"keep": True}).json()
    st = c.get(f"/api/{bid}/state").json()
    ok(3 in st["done"] and st["keep"] == [3] and st["instr"].get("*") == "Giữ thuật ngữ tiếng Anh", "giữ bản gốc trang 4: đã xong, không gọi LLM")
    A.DOCS.clear()                                     # khởi động lại app
    st = c.get(f"/api/{bid}/state").json()
    ok(st["keep"] == [3] and st["skip"] == {"0": [5]} and st["instr"].get("*"), "⛔ / giữ bản gốc / yêu cầu còn sau khi khởi động lại")
    c.post(f"/api/{bid}/page/3/keep", json={"keep": False})
    ok(3 not in c.get(f"/api/{bid}/state").json()["done"], "thôi giữ bản gốc → trang về chưa dịch")
A.chat = _fc

print("12. Sửa sau rà soát (25/09/2026)")
fig = lambda q: bool(A._Q_FIGURE.search(q) or A._Q_FIG.search(q))  # noqa: E731
tab = lambda q: bool(A._Q_TABLE.search(q) or A._Q_TAB.search(q))  # noqa: E731
eq = lambda q: bool(A._Q_FORMULA.search(q) or A._Q_EQ.search(q))  # noqa: E731
ok(not any(fig(q) or tab(q) or eq(q) for q in ["Mô hình này khác BERT thế nào?", "Bảng thuật ngữ dịch attention là gì",
                                                "Mô hình 2 lớp?", "Hình thức huấn luyện?", "Tình hình nghiên cứu?"])
   and all(fig(q) for q in ["Hình 2 nói gì?", "Trong hình, mũi tên chỉ gì?"]) and not fig("Graph neural network là gì?")
   and tab("Bảng 3?") and not fig("Bảng 3?") and eq("Giải thích công thức (1)") and not fig("Giải thích công thức (1)"),
   "“mô hình”, “bảng thuật ngữ”… không bị coi là hỏi về hình/bảng; “Hình 2” là hình, “Bảng 3” là bảng, “công thức” "
   "là công thức (chỉ hình mới cần ảnh)")
with TestClient(A.app) as c:
    with open(ROOT / "data/attention-is-all-you-need.pdf", "rb") as f:
        aid = c.post("/api/upload", files={"file": ("att2.pdf", f, "application/pdf")}).json()["id"]
    d = A.DOCS[aid]
    c.get(f"/api/{aid}/page/3/layer")
    ok("⟦Attention(Q, K, V ) = softmax(QKT √dk )V⟧" in A.page_pair(d, 3, "source"),
       "công thức đứng riêng dòng có trong chữ gửi LLM (Attention, tr.4)")
    real = A.chat_stream
    async def eyes_fail(cfg_, system, messages, num_ctx=0):
        if messages[-1].get("images"):
            raise A.LLMError("Lỗi LLM 429: rate-limited")
        async for x in real(cfg_, system, messages, num_ctx):
            yield x
    A.chat_stream = eyes_fail
    c2 = {**cfg, "see_base_url": "https://see/v1", "see_model": "eye"}
    r = c.post(f"/api/{aid}/chat", json={"cfg": c2, "question": "Hình 2 vẽ gì?", "page": 0})
    info = json.loads(r.headers["x-context"])
    ok(r.text == "Trả lời thử." and info["images"] == [] and info["model"] == "m" and "model xem ảnh lỗi" in info["why"],
       "model xem ảnh lỗi → tự hỏi lại model trò chuyện chỉ với chữ (header nói đúng)")
    A.chat_stream = real
    c.post(f"/api/{aid}/translate/5?span=1", json={**cfg, "instruction": "Giữ nguyên tên riêng", "instruction_scope": "page"})
    r = c.post(f"/api/{aid}/translate/4?span=3", json=cfg).json()
    ok([p["page"] for p in r["pages"]] == [4], "trang có yêu cầu dịch riêng không bị gộp vào nhóm của trang khác")
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question="Viết lại công thức (1)", page=0), "Viết lại công thức (1)", 9000, True)
    ok(P["pages"][0] == 3 and not P["images"] and P.get("formula") == {"pages": [3], "nos": ["1"]},
       "“công thức (1)” → trang có số hiệu (1) (tr.4); gửi ảnh hay không thì chờ nhận dạng công thức xong mới quyết")
    r = c.post(f"/api/{aid}/chat", json={"cfg": c2, "question": "Viết lại công thức (1)", "page": 0})
    info = json.loads(r.headers["x-context"])
    ok(info["images"] == [4] and info["model"] == "eye" and "chưa có LaTeX" in info["why"],
       f"công thức chưa có LaTeX (tắt nhận dạng) → gửi ảnh trang cho model xem ảnh: {info['why']}")
    d.setdefault("flatex", {})[3] = {f["key"]: "x" for f in A.page_display(d, 3)}
    r = c.post(f"/api/{aid}/chat", json={"cfg": c2, "question": "Viết lại công thức (1)", "page": 0})
    info = json.loads(r.headers["x-context"])
    ok(info["images"] == [] and info["model"] == "m" and "đã có LaTeX" in info["why"],
       "công thức đã có LaTeX → model trò chuyện trả lời bằng chữ, không gửi ảnh")
    d["flatex"].pop(3)
    grid = A.table_markdown(A.page_tables(d, 7)[0]["rows"]) if A.page_tables(d, 7) else ""
    ok("| Transformer (big) | **28.4** | **41.8** |" in grid and "3.3 · 10^{18}" in grid and "| Model | BLEU EN-DE |" in grid,
       "Bảng 2 (tr.8) thành lưới đủ số liệu: số mũ 10^{18}, tiêu đề nhiều tầng gộp “BLEU EN-DE”")
    txt = A.page_pair(d, 7, "source", layout=True)
    ok("[bảng]\n| Model |" in txt and "| ConvS2S Ensemble [9] | 26.36 | **41.29** |" in txt and "\n[3] Model\n" not in txt,
       "chữ gửi model trò chuyện có bảng dạng lưới thay cho các ô rời")
    q2 = "Theo Bảng 2, Transformer (big) đạt BLEU bao nhiêu?"
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question=q2, page=0), q2, 9000, True)
    ok(P["pages"][0] == 7 and not P["images"] and "bảng gửi dạng chữ" in P["why"], "hỏi về bảng: gửi bảng dạng chữ, không gửi ảnh")
    real_tables, A.page_tables = A.page_tables, lambda d_, n, page=None: []
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question=q2, page=0), q2, 9000, True)
    A.page_tables = real_tables
    ok(P["images"] == [7] and "không đọc được thành chữ" in P["why"], "bảng không dựng được thành chữ (trang scan) → gửi ảnh trang")
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question="Dịch lại đoạn này", page=7,
                                 selection="Table 2 summarizes our results, see also Figure 1."), "Dịch lại đoạn này", 9000, True)
    ok(not P["images"] and "formula" not in P, "đoạn bôi đen chỉ nhắc tới hình / bảng: không gửi ảnh")
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question="Giải thích", page=3,
                                 selection="Figure 2: (left) Scaled Dot-Product Attention."), "Giải thích", 9000, True)
    ok(P["images"] == [3], "bôi đen chú thích hình rồi hỏi → hỏi về hình → gửi ảnh trang")
    P = A.chat_plan(d, A.ChatAsk(cfg=A.Config(**cfg), question="Hình 1?", page=5, context="near"), "Hình 1?", 9000, True)
    ok(P["pages"] == [5] and not P["images"], "chế độ near (mốc so sánh của bench): chỉ trang đang xem")
    ok(3 - 1 in A.related_pages({"src": ROOT / "data/bert.pdf", "trans": {}}, "BERT-large có bao nhiêu tham số?"),
       "“BERT-large” khớp “BERTLARGE” trong bài")
    A.DOCS.clear()                                     # khởi động lại app: khối nạp từ state.json (toạ độ dạng list)
    r = c.post(f"/api/{aid}/translate/3?span=1&force=true", json=cfg)
    ok(r.status_code == 200, "dịch lại trang có công thức sau khi khởi động lại app (trước đây lỗi 500)")

print("13. Gửi nguyên PDF, tài liệu tham khảo, ⛔ khi trích lại, tổng hợp lại phiên")
OR = "https://openrouter.ai/api/v1"
ok(A.pdf_input(A.Config(base_url=OR, model="nex-agi/nex-n2.5-pro:free")) == "pdf-text"
   and A.pdf_input(A.Config(base_url=OR, model="google/gemini-2.5-flash")) == "native"
   and A.pdf_input(A.Config(base_url="https://api.openai.com/v1", model="gpt-5")) == "native"
   and not A.pdf_input(A.Config(base_url="http://192.168.1.10:10531/v1", model="gpt-6-luna"))
   and not A.pdf_input(A.Config(base_url="http://localhost:11434/v1", model="q")),
   "server nhận file PDF: OpenRouter (pdf-text / native), OpenAI; proxy ChatGPT, Ollama thì không")
with TestClient(A.app) as c:
    with open(ROOT / "data/attention-is-all-you-need.pdf", "rb") as f:
        aid = c.post("/api/upload", files={"file": ("att3.pdf", f, "application/pdf")}).json()["id"]
    d = A.DOCS[aid]
    ok(sorted(A.refs_boxes(d)) == [9, 10, 11], "phụ lục không có chữ “Appendix” (Attention Visualizations, tr.13–15) vẫn dịch")
    ok(sorted(A.refs_boxes({"src": ROOT / "data/bert.pdf"})) == [9, 10, 11], "BERT: tài liệu tham khảo tr.10–12, phụ lục dịch")
    cor = {**cfg, "chat_base_url": OR, "chat_model": "nex-agi/nex-n2.5-pro:free"}
    r = c.post(f"/api/{aid}/chat", json={"cfg": cor, "question": "Bảng 2 có gì?", "page": 0, "context": "pdf"})
    info = json.loads(r.headers["x-context"])
    ok(info["mode"] == "pdf" and info["pdf"] == "pdf-text" and seen["pdf"] and seen["pdf"]["name"].endswith(".pdf")
       and A.base64.b64decode(seen["pdf"]["data"])[:5] == b"%PDF-" and "[Trang" not in seen["msg"] and "file PDF gốc" in seen["system"],
       "OpenRouter: gửi nguyên file PDF, không kèm chữ các trang")
    r = c.post(f"/api/{aid}/chat", json={"cfg": cfg, "question": "Bảng 2 có gì?", "page": 0, "context": "pdf"})
    info = json.loads(r.headers["x-context"])
    ok(info["mode"] == "full" and not seen["pdf"] and "không nhận file PDF" in info["why"] and "[Trang 1 ·" in seen["msg"],
       "server không nhận file → gửi chữ cả bài, lý do trong header")
    async def no_pdf(cfg_, system, messages, num_ctx=0):
        if messages[-1].get("pdf"):
            raise A.LLMError("Lỗi LLM 400: file input not supported")
        async for x in fs(cfg_, system, messages, num_ctx):
            yield x
    A.chat_stream = no_pdf
    r = c.post(f"/api/{aid}/chat", json={"cfg": cor, "question": "Bảng 2 có gì?", "page": 0, "context": "pdf"})
    info = json.loads(r.headers["x-context"])
    ok(r.text == "Trả lời thử." and info["mode"] == "full" and "server lỗi" in info["why"], "server từ chối file → tự hỏi lại bằng chữ")
    A.chat_stream = fs
    # chat_stream thật: phần file + plugin file-parser trong body gửi OpenRouter
    BODY = {}
    def handler(req):
        BODY.update(json.loads(req.content))
        return A.httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
    RealClient = A.httpx.AsyncClient
    class Mock(RealClient):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=A.httpx.MockTransport(handler), **k)
    A.httpx.AsyncClient = Mock
    async def run():
        return "".join([x async for x in REAL_STREAM(A.Config(base_url=OR, model="x/y:free"), "sys",
                                                        [{"role": "user", "content": "hỏi", "pdf": {"name": "a.pdf", "data": "JVBE"}}])])
    out = asyncio.run(run())
    A.httpx.AsyncClient = RealClient
    part = BODY["messages"][-1]["content"][-1]
    ok(out == "ok" and part["type"] == "file" and part["file"]["file_data"].startswith("data:application/pdf;base64,")
       and BODY["plugins"] == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}], "body gửi OpenRouter: phần file + plugin pdf-text")
    # ⛔ khi trích lại khối: số hiệu đổi → tìm lại theo dấu chữ
    blocks = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    d["skip"] = {0: {1: A.block_hash(blocks[1]), 2: A.block_hash({"text": "mất"})}}
    ok(A.user_skips(d, 0, [{"text": "x"}] + blocks, heal=False) == {2} and d["skip"][0].get(1),
       "không sửa dấu khi chỉ là lớp chữ tạm")
    ok(A.user_skips(d, 0, [{"text": "x"}] + blocks) == {2} and d["skip"][0] == {2: A.block_hash(blocks[1])},
       "đoạn ⛔ đổi số hiệu → theo đúng đoạn; đoạn không còn → bỏ dấu")
    d["skip"] = {}
    # tổng hợp lại một phiên → sửa ghi chú cũ, không thêm bản mới
    r = c.post(f"/api/{aid}/chat", json={"cfg": cfg, "question": "Transformer là gì?", "page": 0})
    sid = r.headers["x-session-id"]
    n1 = c.post(f"/api/{aid}/chats/{sid}/summarize", json=cfg).json()
    c.post(f"/api/{aid}/chat", json={"cfg": cfg, "question": "Còn gì nữa?", "session_id": sid, "page": 0})
    n2 = c.post(f"/api/{aid}/chats/{sid}/summarize", json=cfg).json()
    ok(n1["id"] == n2["id"] and sum(x.get("meta", {}).get("session") == sid for x in d["notes"]) == 1,
       "tổng hợp lại phiên: cập nhật đúng ghi chú cũ")

print("14. Công thức → LaTeX")
ok(A.clean_latex("\\(R_{\\Theta}^{d}\\),") == "R_{\\Theta}^{d}" and A.clean_latex("$$a=b$$") == "a=b"
   and A.clean_latex("```latex\nx^2\n```") == "x^2", "bỏ dấu bao công thức, ```, dấu câu chép kèm")
ok(A.latex_matches(r"\cos m\theta_1 & -\sin m\theta_1", "cos mθ1 −sin mθ1")
   and not A.latex_matches(r"\mathbf{re} x", "cos mθ1 −sin mθ1"), "đối chiếu LaTeX với glyph: khớp / chặn chép nhầm")
ok(A.formula_prompt("hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF:latest") == "Formula Recognition:"
   and A.formula_prompt("x/y") == A.FORMULA_PROMPT, "prompt nhận dạng công thức theo model")
FCFG = {**cfg, "formula_ocr": "ocr", "ocr_vlm_base_url": "http://ocr/v1", "ocr_vlm_model": "PaddleOCR-VL"}
REQ, REPLY = [], {}
def fhandler(req):
    body = json.loads(req.content)
    REQ.append(body["messages"][0]["content"][0]["text"])
    return A.httpx.Response(200, json={"choices": [{"message": {"content": REPLY.get("x", "")}, "finish_reason": "stop"}]})
RealClient = A.httpx.AsyncClient
class FMock(RealClient):
    def __init__(self, *a, **k):
        super().__init__(*a, transport=A.httpx.MockTransport(fhandler), **k)
A.httpx.AsyncClient = FMock
with TestClient(A.app) as c:
    with open(ROOT / "data/roformer-rope.pdf", "rb") as f:
        rid = c.post("/api/upload", files={"file": ("rope.pdf", f, "application/pdf")}).json()["id"]
    d = A.DOCS[rid]
    c.get(f"/api/{rid}/page/4/layer")
    disp = A.page_display(d, 4)
    ok([f["no"] for f in disp] == ["(14)", "(15)", "(16)"] and "cos mθ1" in disp[1]["text"],
       f"công thức đứng riêng dòng theo layout, có số hiệu: {[f['no'] for f in disp]}")
    jobs = A.formula_jobs(d, 4)
    ok(len(jobs) > 3, f"cần nhận dạng: 3 công thức riêng dòng + công thức trong dòng nhiều tầng ({len(jobs)})")
    REPLY["x"] = "\\mathbf{re}"                                   # model chép nhầm vùng → không dùng
    r = c.post(f"/api/{rid}/formulas", json={"cfg": FCFG, "pages": [4]}).json()
    ok(r["latex"] == 0 and r["glyph"] == len(jobs) and REQ and REQ[0] == "Formula Recognition:",
       "kết quả không khớp glyph → giữ glyph, không thử lại")
    n_req = len(REQ)
    c.post(f"/api/{rid}/formulas", json={"cfg": FCFG, "pages": [4]})
    ok(len(REQ) == n_req, "đã thử rồi thì không gọi lại")
    d["flatex"] = {}
    real_match = A.latex_matches
    A.latex_matches = lambda latex, glyph: True
    REPLY["x"] = "$$\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$$"
    r = c.post(f"/api/{rid}/formulas", json={"cfg": FCFG, "pages": [4]}).json()
    A.latex_matches = real_match
    txt = A.page_pair(d, 4, "source")
    ok(r["latex"] == len(jobs) and "[công thức (15)] ⟦$$\\begin{pmatrix}" in txt, "LaTeX thay chữ glyph trong ngữ cảnh gửi LLM")
    r = c.post(f"/api/{rid}/chat", json={"cfg": FCFG, "question": "Công thức (15) là gì?", "page": 4, "context": "source"})
    info = json.loads(r.headers["x-context"])
    ok("công thức dạng LaTeX" in info["why"] and "nhận dạng từ ảnh" in seen["system"], "trò chuyện: dùng LaTeX, lý do ghi số công thức")
    A.DOCS.clear()                                     # khởi động lại app
    c.get(f"/api/{rid}/state")
    ok(A.DOCS[rid]["flatex"].get(4) and "\\begin{pmatrix}" in A.page_pair(A.DOCS[rid], 4, "source"),
       "LaTeX lưu trong state.json, còn sau khi khởi động lại")
    def down(req):
        raise A.httpx.ConnectError("refused")
    class Down(RealClient):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=A.httpx.MockTransport(down), **k)
    A.httpx.AsyncClient = Down
    d = A.DOCS[rid]
    r = c.post(f"/api/{rid}/chat", json={"cfg": FCFG, "question": "Trang 6 nói gì?", "page": 5})
    ok(r.status_code == 200 and r.text == "Trả lời thử." and "flatex_fail" in d,
       "server nhận dạng không chạy → vẫn trả lời bằng glyph, 10 phút không thử lại")
A.httpx.AsyncClient = RealClient

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
items = [{"id": 0, "text": "the auto-regressive property of {v1}"}, {"id": 1, "text": "Results (a), b."},
         {"id": 2, "text": "As shown in Figure 1, we denote input embedding as {v1}."}, {"id": 3, "text": "北京 is big"},
         {"id": 4, "text": "2 Background and Related Work"}, {"id": 5, "text": "3.2.2 Multi-Head Attention"}]
got = A.keep_formulas(items, {0: "tính tự回归 của {v1}", 1: "Kết quả（a），b。", 3: "北京 rất lớn",
                              2: "Như Hình 1, we denote input embedding as {v1}.", 4: "2 Background and Related Work",
                              5: "3.2.2 Multi-Head Attention"})
ok(set(got) == {1, 3, 5} and got[1] == "Kết quả (a), b.",
   "kiểm bản dịch: loại chữ Hán lạc, nửa câu/tiêu đề còn nguyên tiếng Anh; đổi dấu toàn khổ; giữ tên riêng, thuật ngữ")
m = A.model_meta({"id": "a/b:free", "context_length": 262144, "architecture": {"input_modalities": ["text", "image"]},
                  "pricing": {"prompt": "0", "completion": "0"}})
m2 = A.model_meta({"id": "local"})
ok(m == {"id": "a/b:free", "name": "", "ctx": 262144, "vision": True, "free": True} and m2["vision"] is None and m2["free"] is None,
   "thông tin model: OpenRouter có ctx/ảnh/free, server khác để trống (không đoán)")

print("15. Dịch hàng loạt bằng dòng lệnh (scripts/batch_translate.py)")
sys.path.insert(0, str(ROOT / "scripts"))
import batch_translate as BT  # noqa: E402


async def batch(*argv):
    """Chạy như dòng lệnh, nhưng gọi thẳng app trong tiến trình này (kho tạm) thay vì app đang chạy ở cổng 8000."""
    ap_args = BT.argparse.Namespace(paths=[str(ROOT / "data/gqa.pdf")], pages="", force=False, out=str(outdir),
                                    format=["translated"], jobs=2, span=2, model="", base_url="", translator="",
                                    config="", set=["base_url=http://x/v1", "model=m", "ocr_mode=off",
                                                    "google_fallback=false", "formula_ocr=off"], effective={},
                                    provider="openrouter")
    for kv in argv:
        k, v = kv.split("=", 1)
        setattr(ap_args, k, v.split(",") if k == "format" else [v] if k == "set" else v)
    async with A.httpx.AsyncClient(transport=A.httpx.ASGITransport(app=A.app), base_url="http://t",
                                   timeout=None) as c:
        cfg = await BT.build_config(c, ap_args)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            err = await BT.translate_doc(c, pathlib.Path(ap_args.paths[0]), cfg, ap_args)
        pdf = pathlib.Path(ap_args.paths[0])   # tải lên lần nữa = lấy lại đúng tài liệu (cùng sha256)
        doc = (await c.post("/api/upload", files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")})).json()["id"]
        done = (await c.get(f"/api/{doc}/state")).json()["done"]
    return err, done, log.getvalue()


outdir = A.store.ROOT / "batch-out"
err, done, log = asyncio.run(batch("pages=1-2"))
ok(err == 0 and done == [0, 1] and (outdir / "gqa_vi.pdf").exists(), "dịch dải trang, lưu bản dịch cạnh --out")
err, done, log = asyncio.run(batch("pages=1-3", "format=translated,dual-alt"))
ok(err == 0 and done == [0, 1, 2] and "2 trang đã dịch từ trước, cần dịch 1" in log and (outdir / "gqa_song_ngu_xen_trang.pdf").exists(),
   "chạy lại: chỉ dịch trang còn thiếu (app nhận ra file đã tải lên), tải thêm bản song ngữ")
try:
    asyncio.run(batch("set=modle=x"))
    ok(False, "gõ nhầm tên trường cấu hình phải báo lỗi")
except SystemExit as e:
    ok("modle" in str(e), "gõ nhầm tên trường cấu hình → báo lỗi, không lặng lẽ bỏ qua")
async def cfg_of(provider, *sets):
    ns = BT.argparse.Namespace(provider=provider, config="", set=list(sets), model="", base_url="", translator="",
                               effective={})
    async with A.httpx.AsyncClient(transport=A.httpx.ASGITransport(app=A.app), base_url="http://t") as c:
        return await BT.build_config(c, ns)
dflt, tuf = asyncio.run(cfg_of("openrouter")), asyncio.run(cfg_of("tuf"))
# model free của OpenRouter không phải lúc nào cũng có đuôi ":free" (ling-3.1-flash, model ẩn danh "stealth/…" giá 0)
# → chỉ kiểm script và app dùng cùng một mặc định
ok(dflt == {"base_url": "https://openrouter.ai/api/v1", "model": A.FREE_MODEL} and A.Config().model == A.FREE_MODEL,
   "mặc định (script và app): OpenRouter, FREE_MODEL")
ok(tuf["base_url"].endswith(":8080/v1") and tuf["model"] == "4b" and asyncio.run(cfg_of("tuf", "model=9b"))["model"] == "9b",
   "--provider tuf: Base URL + model của nhà cung cấp đã lưu; --set/--model vẫn đè được")
try:
    asyncio.run(cfg_of("khong-co"))
    ok(False, "nhà cung cấp không có phải báo lỗi")
except SystemExit as e:
    ok("openrouter" in str(e), "nhà cung cấp không có → báo lỗi kèm danh sách")
ok(BT.parse_range("2-3,5", 6) == [1, 2, 4] and BT.parse_range("5-", 6) == [4, 5] and BT.parse_range("", 2) == [0, 1],
   "dải trang: 2-3,5 / 5- / rỗng")

print("16. Số dòng ở lề (bản nộp ICLR/ACL: 000, 001… cả hai lề)")
ln = pymupdf.open()
ln.insert_pdf(pymupdf.open(ROOT / "data/bert.pdf"), from_page=2, to_page=2)
pg = ln[0]
xs = [b["bbox"] for b in pg.get_text("dict")["blocks"] if b.get("lines")]
L, R, y, k = min(b[0] for b in xs), max(b[2] for b in xs), 80.0, 54
while y < pg.rect.height - 60:
    for x in (L - 24, R + 10):
        pg.insert_text((x, y), f"{k:03d}", fontsize=7, fontname="tiro", color=(0.5, 0.5, 0.5))
    y, k = y + 10.9, k + 1
got = A.extract_blocks(pg)
ok(got and not any(re.search(r"(?<![\d.])0\d\d(?![\d.])", b["text"]) for b in got),
   "số dòng ở lề không lẫn vào đoạn nào, không thành đoạn riêng để dịch")

print("\nTẤT CẢ ĐỀU ĐẠT" if not FAIL else f"\n{len(FAIL)} MỤC SAI: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
