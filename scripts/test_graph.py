"""Kiểm tab Sơ đồ (graphs.py + thư viện paper-graph) KHÔNG cần OpenRouter: lượt dựng thay bằng hàm chép sơ đồ RAG
đã dựng sẵn của paper-graph, thư mục lưu tạm, không đụng data/docs/ hay data/keys.json.

    .venv/bin/python scripts/test_graph.py      # ~1 phút (model bố cục chạy trên 20 trang)

Mục 8 (listener postMessage của static/graph.js) chạy scripts/test_graph_js.mjs bằng node, bỏ qua nếu máy không có node.

In ✓/✗ cho từng mục, mã thoát 1 nếu có mục trượt."""
import atexit
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAG = ROOT.parent / "paper-graph" / "papers" / "rag" / "rag.graph.json"
A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-graph-"))
atexit.register(shutil.rmtree, A.store.ROOT, ignore_errors=True)
A.KEYS_FILE, A._keys = A.store.ROOT / "keys.json", {}
A.load_keys = lambda: None
G = A.G
FAIL = []
KEY = "sk-or-v1-" + "k" * 40


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


def data_of(html: str) -> dict:
    return json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1))


if not G.AVAILABLE or not RAG.exists():
    print("cần paper-graph ở ../paper-graph (pip install -e ../paper-graph) và papers/rag/rag.graph.json")
    sys.exit(1)

BUILT = []


def fake_build(doc_id, model, key):
    """Thay lượt gọi OpenRouter: chép sơ đồ RAG (dựng từ cùng PDF) sang tên file của VietPaper."""
    BUILT.append((doc_id, model, key))
    p = G.paths(doc_id)
    g = json.loads(RAG.read_text("utf-8"))
    g["paper"]["blocks"] = p.blocks.name
    p.graph.write_text(json.dumps(g, ensure_ascii=False), "utf-8")
    G.say(f"OpenRouter trả 429 cho khoá {key}", key)
    return True, p.graph.name


cfg = {"base_url": "http://x/v1", "model": "m", "glossary_auto": False, "translator": "llm"}
with A.test_client() as c:
    print("1. Chưa có sơ đồ")
    with open(ROOT / "data/rag.pdf", "rb") as f:
        did = c.post("/api/upload", files={"file": ("rag.pdf", f, "application/pdf")}).json()["id"]
    D = A.DOCS[did]
    st = c.get(f"/api/{did}/graph").json()
    ok(st["available"] and not st["graph"] and st["job"] is None, "trạng thái: có thư viện, chưa có sơ đồ")
    ok(c.get(f"/api/{did}/graph/view").status_code == 404, "xem khi chưa có sơ đồ → 404")
    ok(c.get("/api/zzz/graph").status_code == 404 and c.get("/api/../graph").status_code == 404, "id lạ → 404 (TM-17)")

    print("2. Dựng: chốt chặn trước khi gọi model (TM-15, TM-16)")
    body = {"cfg": cfg, "allow_training": True}
    r = c.post(f"/api/{did}/graph/build", json={**body, "model": "anthropic/claude-opus-5.5"})
    ok(r.status_code == 400 and ":free" in r.json()["detail"], "model không :free → 400")
    r = c.post(f"/api/{did}/graph/build", json={**body, "allow_training": False})
    ok(r.status_code == 400, "chưa xác nhận gửi chữ bài → 400")
    r = c.post(f"/api/{did}/graph/build", json=body)
    ok(r.status_code == 400 and "key OpenRouter" in r.json()["detail"], "chưa có key OpenRouter → 400")
    A._keys[G.OPENROUTER] = KEY
    real_build, G.run_build = G.run_build, fake_build
    r = c.post(f"/api/{did}/graph/build", json=body)
    ok(r.status_code == 200 and r.json()["job"]["running"], "dựng bằng model mặc định :free → chạy nền")
    ok(c.post(f"/api/{did}/graph/build", json=body).status_code == 409, "bấm lần nữa khi đang dựng → 409")
    for _ in range(1500):   # lần đầu còn nạp model bố cục
        if not (st := c.get(f"/api/{did}/graph").json())["job"]["running"]:
            break
        time.sleep(0.2)
    job = st["job"]
    if not job["ok"]:
        print("    log:", job["log"][-5:], "running:", job["running"])
    ok(job["ok"] is True and st["graph"], f"dựng xong, có sơ đồ ({job.get('error') or job['out']})")
    ok(BUILT and BUILT[0][1] == A.G.pg_build.DEFAULT_MODEL and BUILT[0][2] == KEY, "dùng key đã nhớ, model mặc định")
    ok(not any(KEY in line for line in job["log"]) and any("[khoá]" in line for line in job["log"]),
       "log trả về giao diện không chứa key (TM-16)")
    p = G.paths(did)
    blocks = json.loads(p.blocks.read_text("utf-8"))
    ok(len(D["blocks"]) == D["out"].page_count == blocks["source"]["pages"], "mọi trang đã trích và lưu khối")
    ids, _ = G._ids(p)
    n, k = map(int, re.fullmatch(r"p(\d+)-b(\d+)", blocks["blocks"][5]["id"]).groups())
    ok(set(ids) == {b["id"] for b in blocks["blocks"]} and G._mark(D["blocks"][n - 1][1][k - 1]) == ids[f"p{n}-b{k}"],
       "id p{n}-b{k} là đoạn k-1 của trang n-1 trong VietPaper")
    ok(st["stale"] == [], "chưa trang nào đổi khối")

    print("3. Trình xem nhúng (TM-14)")
    r = c.get(f"/api/{did}/graph/view")
    html = r.text
    ok(r.status_code == 200 and '<script src="/pg/viewer.js"></script>' in html, "HTML nạp viewer.js từ app")
    ok(re.findall(r"<script(?: [^>]*)?>", html) == ['<script type="application/json" id="data">',
                                                    '<script src="/pg/viewer.js">'], "không script nội tuyến")
    ok("script-src 'self'" in r.headers["content-security-policy"], "CSP của app áp vào trang")
    data = data_of(html)
    ok(data["embed"] and data["edit"]["api"] == f"/api/{did}/graph/" and len(data["nodes"]) >= 40,
       f"dữ liệu có chế độ nhúng, route sửa của bài ({len(data['nodes'])} ô)")
    ok(c.get("/pg/viewer.js").status_code == 200 and c.get("/pg/viewer.css").status_code == 200
       and c.get("/pg/template.html").status_code == 404 and c.get("/pg/..%2Fbuild.py").status_code == 404,
       "/pg/ chỉ phục vụ viewer.js, viewer.css")

    print("4. Bản dịch câu trích")
    texts = {b["id"]: b["text"] for b in blocks["blocks"]}
    anchor = next(a for nd in data["nodes"].values() for a in nd["anchors"]
                  if a["block"] and len(G._sentences(texts[a["block"]])) >= 3
                  and len(G._sentences(a["quote"])) == 1 and a["quote"].rstrip()[-1:] not in ".!?")
    n, k = map(int, re.fullmatch(r"p(\d+)-b(\d+)", anchor["block"]).groups())
    sents = G._sentences(texts[anchor["block"]])
    D["done"].add(n - 1)
    D["trans"].setdefault(n - 1, {})[k - 1] = " ".join(f"Câu dịch số {i}." for i in range(len(sents)))
    want = [i for i, s in enumerate(sents) if A.G.squash(anchor["quote"]) in A.G.squash(s)]
    got = next(a for a in data_of(c.get(f"/api/{did}/graph/view").text)["nodes"][
        next(i for i, nd in data["nodes"].items() if anchor in nd["anchors"])]["anchors"] if a["quote"] == anchor["quote"])
    ok(want and got.get("vi") == f"Câu dịch số {want[0]}." and got.get("vi_whole") is False,
       f"câu trích nằm trong câu {want[:1]} của đoạn → đúng câu đó của bản dịch")
    D["trans"][n - 1][k - 1] = "Bản dịch gộp hết thành một câu"
    vi = G.translation(D, ids, texts[anchor["block"]], anchor["block"], anchor["quote"])
    ok(vi == ("Bản dịch gộp hết thành một câu", True), "số câu lệch → cả đoạn, có cờ vi_whole")
    old = D["blocks"][n - 1][1][k - 1]
    D["blocks"][n - 1][1][k - 1] = dict(old, text="chữ khác hẳn")
    ok(G.translation(D, ids, texts[anchor["block"]], anchor["block"], anchor["quote"]) is None
       and c.get(f"/api/{did}/graph").json()["stale"] == [n], "khối trang đó đổi → bỏ bản dịch, báo trang lệch")
    D["blocks"][n - 1][1][k - 1] = old
    marks, formulas = G._ids(p)
    fid = next(b for b in formulas if formulas[b] and all(formulas[b]))
    fn, fk = map(int, re.fullmatch(r"p(\d+)-b(\d+)", fid).groups())
    D["done"].add(fn - 1)
    D["trans"].setdefault(fn - 1, {})[fk - 1] = "Công thức {v1} và { V1 } ở đây, {v99} thì không có."
    vi = G.translation(D, marks, texts[fid], fid, "khong khop", formulas[fid])
    ok(vi and "{" not in vi[0] and vi[0].count(formulas[fid][0]) == 2 and "…" in vi[0],
       f"chỗ giữ chỗ công thức trong bản dịch thay bằng chữ công thức ({formulas[fid][0]!r}), số lạ thành …")

    print("5. Sửa trong trình xem (TM-17)")
    tok, ver = data["edit"]["token"], data["edit"]["version"]
    nid = next(iter(data["nodes"]))
    url = f"/api/{did}/graph/edit"
    hdr = {"X-PG-Token": tok}
    r = c.post(url, json={"node": nid, "set": {"status": "locked"}, "version": ver}, headers=hdr)
    ok(r.status_code == 200 and r.json()["nodes"][nid]["status"] == "locked", "khoá ô → 200, ghi vào sơ đồ")
    ok(json.loads(p.graph.read_text("utf-8"))["nodes"][0]["status"] == "locked", "paper.graph.json đã đổi")
    ok(c.post(url, json={"node": nid, "set": {"status": "accepted"}, "version": ver}, headers=hdr).status_code == 409,
       "version cũ → 409")
    ver2 = r.json()["version"]
    ok(c.post(url, json={"node": nid, "set": {"status": "accepted"}, "version": ver2},
              headers={"X-PG-Token": "sai"}).status_code == 403, "sai token → 403")
    ok(c.post(url, content=b"{}", headers={**hdr, "Content-Type": "text/plain"}).status_code == 415, "không phải JSON → 415")
    ok(c.post(url, content=b"[" * 70000, headers={**hdr, "Content-Type": "application/json"}).status_code == 413,
       "thân quá 64 KB → 413")
    evil = "Giải thích </script><img src=x onerror=alert(document.cookie)> hết"  # không chữ số: PG220
    r = c.post(url, json={"node": nid, "set": {"status": "accepted"}, "version": ver2}, headers=hdr)  # mở khoá
    r = c.post(url, json={"node": nid, "set": {"explain": evil}, "version": r.json()["version"]}, headers=hdr)
    html = c.get(f"/api/{did}/graph/view").text
    ok(r.status_code == 200 and "<img src=x" not in html and evil in json.dumps(data_of(html), ensure_ascii=False),
       f"chữ có thẻ HTML lưu được nhưng không thành thẻ trong trang ({r.status_code})")
    r = c.post(f"/api/{did}/graph/move", json={"tier": "", "reset": True, "version": data_of(html)["edit"]["version"]},
               headers=hdr)
    ok(r.status_code == 200, "dàn lại tầng đầu → 200")

    print("6. Dựng lại không đạt: sơ đồ cũ vẫn đi với các đoạn cũ")
    before = p.blocks.read_bytes()
    G.keep_old(p)
    p.blocks.write_text("{}", "utf-8")
    G.restore(p, False)
    ok(p.blocks.read_bytes() == before and not p.blocks.with_name(p.blocks.name + ".prev").exists(),
       "không đạt → đặt lại paper.blocks.json cũ, xoá bản .prev")
    G.keep_old(p)
    p.blocks.write_text("{}", "utf-8")
    G.restore(p, True)
    ok(p.blocks.read_text("utf-8") == "{}", "đạt → giữ bản mới")
    p.blocks.write_bytes(before)

    def boom(doc_id, model, key):
        p.blocks.write_text("{}", "utf-8")   # giả lập các đoạn đã đổi rồi lượt dựng hỏng
        raise RuntimeError(f"hết lượt free với khoá {key}")
    G.run_build = boom
    c.post(f"/api/{did}/graph/build", json=body)
    for _ in range(1500):
        if not (st := c.get(f"/api/{did}/graph").json())["job"]["running"]:
            break
        time.sleep(0.2)
    ok(st["job"]["ok"] is False and KEY not in st["job"]["error"] and p.blocks.read_bytes() == before
       and c.get(f"/api/{did}/graph/view").status_code == 200,
       "lượt dựng lỗi → báo lỗi (không lộ key), các đoạn cũ được đặt lại, sơ đồ cũ vẫn mở được")

    print("7. Xoá tài liệu")
    c.delete(f"/api/docs/{did}")
    ok(not p.graph.exists() and did not in G._apps, "xoá bài xoá luôn sơ đồ và trạng thái trình xem")
    G.run_build = real_build

print("8. Bấm ô → cuộn tới đoạn: listener postMessage của static/graph.js (TM-18), chạy bằng node")
node = shutil.which("node")
if node:
    try:
        r = subprocess.run([node, str(ROOT / "scripts" / "test_graph_js.mjs")], capture_output=True, text=True, timeout=60)
        print("\n".join("  " + x for x in r.stdout.rstrip().splitlines()[:-1] if x.strip()))
        ok(r.returncode == 0, f"scripts/test_graph_js.mjs mã {r.returncode} {r.stderr.strip()[-300:]}")
    except subprocess.TimeoutExpired:
        ok(False, "scripts/test_graph_js.mjs treo quá 60 giây")
else:   # máy chạy app (TUF) không có node: chạy mục này trên máy phát triển
    print("  - bỏ qua: máy này không có node (chạy `node scripts/test_graph_js.mjs` trên máy có node)")

print("\n" + ("TẤT CẢ ĐẠT" if not FAIL else f"{len(FAIL)} MỤC TRƯỢT"))
sys.exit(1 if FAIL else 0)
