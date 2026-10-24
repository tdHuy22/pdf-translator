"""Thử THẬT nút "Dựng sơ đồ" bằng 🌐 API (OpenRouter, chỉ model `:free`; tốn hạn mức free trong ngày, không tốn tiền).
Thư mục lưu tạm, không đụng data/docs/; key OpenRouter lấy từ key đã nhớ trong data/keys.json, chỉ đọc (không ghi đè
file đó, không gửi api_key trong Config). Cần đã bật "Free model training" trên OpenRouter.

    .venv/bin/python scripts/live_graph_api.py                     # bài GQA (7 trang), model mặc định của paper-graph
    .venv/bin/python scripts/live_graph_api.py --pdf data/x.pdf --model nvidia/nemotron-3-super-120b-a12b:free

Nhà cung cấp free hay trả 502/429: lượt dựng chết vì lỗi đó (đã qua các lần thử lại và model dự phòng của
paper-graph) thì dựng lại (`--tries`, mặc định 3), trừ khi hết hạn mức ngày. In ✓/✗, thời
gian, số ô; mã thoát 1 nếu trượt."""
import argparse
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
ap = argparse.ArgumentParser()
ap.add_argument("--pdf", default=str(ROOT / "data/gqa.pdf"))
ap.add_argument("--model", default="", help="model :free (mặc định: của paper-graph, có model dự phòng)")
ap.add_argument("--tries", type=int, default=3)
ap.add_argument("--keep", help="chép paper.graph(.draft).json và log vào thư mục này")
args = ap.parse_args()

key = A.remember(A.G.OPENROUTER, "")   # đọc key đã nhớ trước khi trỏ KEYS_FILE ra thư mục tạm
if not key:
    sys.exit("Chưa có key OpenRouter trong data/keys.json (nhập trong ⚙️ Cấu hình của app)")
TMP = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-live-graph-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
A.store.ROOT = TMP / "docs"
A.KEYS_FILE, A._keys = TMP / "keys.json", {A.G.OPENROUTER: key}   # chỉ trong bộ nhớ; remember("", ...) không ghi file
A.load_keys = lambda: None
A.USER_TERMS_FILE = TMP / "user_terms.json"
A.PROVIDERS_FILE = TMP / "providers.json"
A.MODEL_META_FILE = TMP / "model_meta.json"
CFG = {"base_url": A.G.OPENROUTER}   # như giao diện, không kèm api_key
FAIL = []


def ok(cond, msg, t0=None):
    took = f" ({time.time() - t0:.0f} s)" if t0 else ""
    print(("  ✓ " if cond else "  ✗ ") + msg + took, flush=True)
    if not cond:
        FAIL.append(msg)


def depth(par: dict, i: str) -> int:
    n = 1
    while par.get(i) is not None and n < 20:
        i, n = par[i], n + 1
    return n


def transient(job: dict) -> bool:
    """Lượt dựng chết vì lỗi tạm của nhà cung cấp (429, 502…, hết giờ): đúng luật thử lại của paper-graph, trừ hết hạn
    mức ngày. Lượt dựng chạy xong mà sơ đồ chưa đạt thì không thử lại (tốn tới 32 lượt free mỗi lần)."""
    msg = re.sub(r"^\w+: ", "", job.get("error") or "")   # graph_job ghi "BuildError: <lỗi>"
    return bool(msg) and A.G.pg_build._retryable(A.G.pg_build.BuildError(msg))


with A.test_client() as c:
    with open(args.pdf, "rb") as f:
        did = c.post("/api/upload", files={"file": (pathlib.Path(args.pdf).name, f, "application/pdf")}).json()["id"]
    p = A.G.paths(did)
    print(f"Dựng sơ đồ bằng 🌐 API: {pathlib.Path(args.pdf).name}, model {args.model or A.G.pg_build.DEFAULT_MODEL}")
    job, took = {}, 0.0
    for n in range(1, args.tries + 1):
        t = time.time()
        r = c.post(f"/api/{did}/graph/build", json={"cfg": CFG, "source": "api", "allow_training": True,
                                                     "model": args.model})
        if r.status_code != 200:
            ok(False, f"POST /graph/build → {r.status_code} {r.text[:200]}")
            break
        seen = 0
        while (st := c.get(f"/api/{did}/graph").json())["job"]["running"]:
            log = st["job"].get("log") or []
            for line in log[seen:]:
                print("    " + line, flush=True)
            seen = len(log)
            time.sleep(5)
        job, took = st["job"], time.time() - t
        for line in (job.get("log") or [])[seen:]:
            print("    " + line, flush=True)
        if job.get("ok") or n == args.tries or not transient(job):
            break
        print(f"  … lần {n} lỗi tạm của nhà cung cấp free, thử lại sau 60 s", flush=True)
        time.sleep(60)
    ok(job.get("ok") and p.graph.exists(), f"lần {n}: {job.get('out') or job.get('error', '')[:200]}", t)
    if p.graph.exists():
        doc = json.loads(p.graph.read_text("utf-8"))
        par = {nd["id"]: nd.get("parent") for nd in doc.get("nodes", [])}
        chk = subprocess.run([sys.executable, "-m", "paper_graph", "check", str(p.graph), "--pdf", args.pdf],
                             capture_output=True, text=True)
        ok(chk.returncode == 0, f"paper_graph check: mã {chk.returncode}; {chk.stdout.strip().splitlines()[-1:] or chk.stderr[-200:]}")
        ok(c.get(f"/api/{did}/graph/view").status_code == 200, "GET /graph/view 200")
        print(f"  {len(par)} ô ({sum(v is None for v in par.values())} ở tầng đầu, sâu "
              f"{max((depth(par, i) for i in par), default=0)} tầng), {len(doc.get('edges', []))} cạnh, "
              f"{took / 60:.1f} phút, model {job.get('model')}")
    if args.keep:
        out = pathlib.Path(args.keep)
        out.mkdir(parents=True, exist_ok=True)
        for f in (p.graph, p.draft, p.blocks):
            if f.exists():
                shutil.copy2(f, out / f.name)
        (out / "log.txt").write_text("\n".join(job.get("log") or []), "utf-8")

print("\n" + ("TẤT CẢ ĐẠT" if not FAIL else f"{len(FAIL)} MỤC TRƯỢT: " + "; ".join(FAIL)))
sys.exit(1 if FAIL else 0)
