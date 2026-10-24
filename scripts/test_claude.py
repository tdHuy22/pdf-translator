"""Kiểm lớp Claude Code (claude_code.py, dựng sơ đồ bằng Claude trong graphs.py) KHÔNG cần đăng nhập Claude: một
chương trình `claude` giả (ghi lại tham số, môi trường, stdin rồi trả sự kiện stream-json) thay cho CLI thật, thư mục
lưu tạm, không đụng data/docs/, data/keys.json hay ~/.claude/projects/ thật.

    .venv/bin/python scripts/test_claude.py      # ~30 giây

In ✓/✗ cho từng mục, mã thoát 1 nếu có mục trượt."""
import asyncio
import atexit
import base64
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as A

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-claude-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
A.store.ROOT = TMP / "docs"
A.KEYS_FILE, A._keys = TMP / "keys.json", {}
A.load_keys = lambda: None
CC = A.CC
CC.WORK = TMP / "claude-work"
LOG = TMP / "calls.jsonl"
FAKE = TMP / "claude"
FAKE.write_text(f"""#!{sys.executable}
import json, os, re, sys, time
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit()
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai"}})); sys.exit()
data = sys.stdin.read()
sysf = args[args.index("--system-prompt-file") + 1] if "--system-prompt-file" in args else None
with open({str(LOG)!r}, "a") as f:
    f.write(json.dumps({{"args": args, "env": sorted(os.environ),
                        "cwd": os.getcwd(), "stdin": data[:200000],
                        "system": open(sysf).read() if sysf else None}}) + "\\n")
mode = open({str(TMP / "mode")!r}).read().strip() if os.path.exists({str(TMP / "mode")!r}) else "ok"
if mode == "sleep":
    time.sleep(60)
if mode.startswith("build"):   # agent dựng sơ đồ giả: ghi sơ đồ đã chuẩn bị; "build-evil" còn xoá bản sao sơ đồ cũ
    import shutil
    shutil.copy({str(TMP / "build_graph.json")!r}, "paper.graph.json")
    if mode == "build-evil":
        open("base.graph.json", "w").write("{{}}")
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "xong"}}))
    sys.exit()
if mode == "spawn":   # tiến trình con (như lệnh shell của agent) rồi treo: hết giờ phải chết cả hai
    import subprocess
    child = subprocess.Popen(["sleep", "60"])
    open({str(TMP / "child.pid")!r}, "w").write(str(child.pid))
    time.sleep(60)
if mode == "flood":   # agent dựng sơ đồ in sự kiện dồn dập 30 giây, có tiến trình con: hạn chót vẫn phải dừng nó
    import subprocess
    child = subprocess.Popen(["sleep", "60"])
    open({str(TMP / "child.pid")!r}, "w").write(str(child.pid))
    ev = json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Read", "input": {{"file_path": "paper.blocks.json"}}}}]}}}})
    end = time.time() + 30
    while time.time() < end:
        print(ev, flush=True)
        time.sleep(0.01)
    sys.exit()
if mode == "nologin":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}}))
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("boom: lỗi giả\\n"); sys.exit(3)
try:
    msg = json.loads(data.splitlines()[0])["message"]["content"]
except Exception:
    msg = [{{"type": "text", "text": data}}]
text = " ".join(b.get("text", "") for b in msg if b.get("type") == "text")
kinds = [b.get("type") for b in msg]
out = {{"type": "result", "subtype": "success", "is_error": False, "result": "", "usage": {{"input_tokens": max(11, len(data) // 3), "output_tokens": 7}}}}
try:
    payload = json.loads(text[text.index("{{"):])
except ValueError:
    payload = {{}}
items = isinstance(payload, dict) and isinstance(payload.get("items"), list)
if "--json-schema" in args or items:   # yêu cầu dịch (JSON trong prompt) như model thật làm theo system prompt
    if items:
        out["structured_output"] = {{"items": [{{"id": it["id"], "text": re.sub(r"[A-Za-z]{{2,}}", "từ", it["text"])}}
                                               for it in payload["items"]]}}
    else:
        out["structured_output"] = {{"ok": True, "kinds": kinds}}
    out["result"] = json.dumps(out["structured_output"])
else:
    for piece in ["Xin ", "chào ", "từ Claude"]:
        print(json.dumps({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": piece}}}}}}), flush=True)
    out["result"] = "Xin chào từ Claude | " + ",".join(kinds)
print(json.dumps(out))
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
os.environ["CLAUDE_BIN"] = str(FAKE)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-khong-duoc-lot"
os.environ["ANTHROPIC_BASE_URL"] = "https://evil.example"
os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
os.environ["AWS_SECRET_ACCESS_KEY"] = "khong-duoc-lot"
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat-gia"
FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


def calls() -> list[dict]:
    return [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []


def calls_dirs() -> list:   # thư mục làm việc của lượt gọi còn sót (bỏ qua .cctmp, thư mục tạm chung của Claude Code)
    return [d for d in CC.WORK.iterdir() if not d.name.startswith(".")] if CC.WORK.exists() else []


def mode(m: str) -> None:   # chế độ của claude giả qua file: môi trường tiến trình con chỉ theo danh sách cho phép
    (TMP / "mode").write_text(m)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # còn là zombie chưa ai thu thì coi như đã chết
    return "Z" not in subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout


def reset():
    LOG.unlink(missing_ok=True)
    (TMP / "mode").unlink(missing_ok=True)


PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
PDF = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()


async def post(body: dict, stream: bool = False):
    async with A.llm_client(timeout=60) as c:
        if stream:
            async with c.stream("POST", CC.BASE + "/chat/completions", json=body) as r:
                return r.status_code, [line async for line in r.aiter_lines() if line]
        r = await c.post(CC.BASE + "/chat/completions", json=body)
        return r.status_code, r.json()


print("1. Đổi request kiểu OpenAI → claude -p")
job = CC.convert({"model": "claude-sonnet-5-5", "messages": [
    {"role": "system", "content": "Bạn là người dịch."},
    {"role": "user", "content": "câu hỏi một"}, {"role": "assistant", "content": "trả lời một"},
    {"role": "user", "content": [{"type": "text", "text": "xem ảnh và PDF"}, {"type": "image_url", "image_url": {"url": PNG}},
                                 {"type": "file", "file": {"filename": "a.pdf", "file_data": PDF}}]}],
    "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object", "properties": {"x": {"type": "string"}}}}},
    "reasoning_effort": "none"})
blocks = job["message"]["message"]["content"]
ok(job["system"] == "Bạn là người dịch." and job["effort"] == "low" and job["schema"]["properties"] == {"x": {"type": "string"}},
   "system prompt, mức suy nghĩ, khuôn JSON")
ok([b["type"] for b in blocks] == ["text", "text", "text", "image", "document"]
   and "NGƯỜI DÙNG: câu hỏi một" in blocks[0]["text"] and "TRỢ LÝ: trả lời một" in blocks[0]["text"],
   "lịch sử thành chữ, ảnh thành khối image, PDF thành khối document")
for bad in ({"model": "--dangerously-skip-permissions", "messages": [{"role": "user", "content": "x"}]},
            {"model": "claude-sonnet-5-5", "messages": []}):
    try:
        CC.convert(bad)
        ok(False, f"request lạ bị từ chối: {str(bad)[:60]}")
    except CC.BadRequest:
        ok(True, f"request lạ bị từ chối: {str(bad)[:60]}")
a = CC.argv("claude", job, pathlib.Path("/tmp/s.txt"))
ok(a[a.index("--tools") + 1] == "" and "--safe-mode" in a and "--restricted" in a and "--no-session-persistence" in a
   and a[a.index("--permission-mode") + 1] == "dontAsk" and a[a.index("--system-prompt-file") + 1] == "/tmp/s.txt",
   "lượt gọi thường: không công cụ, --safe-mode, không lưu phiên, system prompt qua file (TM-19, TM-20, TM-23)")

print("2. Gọi qua httpx như code của app (claude giả)")
reset()
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [
    {"role": "system", "content": "SYS-PROMPT-" + "x" * 200_000}, {"role": "user", "content": [
        {"type": "text", "text": "đọc"}, {"type": "image_url", "image_url": {"url": PNG}}]}]}))
c0 = calls()[-1]
ok(st == 200 and r["choices"][0]["message"]["content"] == "Xin chào từ Claude | text,image" and r["usage"]["completion_tokens"] == 7,
   "câu trả lời kiểu chat.completion, ảnh tới được CLI")
ok(not any(k.startswith(("ANTHROPIC", "AWS_")) or k == "CLAUDE_CODE_USE_BEDROCK" for k in c0["env"])
   and "CLAUDE_CODE_OAUTH_TOKEN" in c0["env"] and "PATH" in c0["env"],
   "môi trường con theo danh sách cho phép: không ANTHROPIC_*, AWS_*, Bedrock; giữ token setup-token (TM-21)")
ok(c0["system"].startswith("SYS-PROMPT-") and len(c0["system"]) > 200_000 and not any("SYS-PROMPT" in x for x in c0["args"]),
   "system prompt 200 KB qua file, không nằm trong argv (TM-20)")
ok(not calls_dirs(), "không còn thư mục làm việc tạm sau lượt gọi (TM-23)")
st, lines = asyncio.run(post({"model": "claude-haiku-5-5", "stream": True, "messages": [{"role": "user", "content": "chào"}]}, True))
text = "".join((json.loads(x[6:])["choices"][0]["delta"].get("content") or "") for x in lines if x.startswith("data: {"))
ok(st == 200 and text == "Xin chào từ Claude" and lines[-1] == "data: [DONE]", "stream SSE từng mẩu, kết thúc [DONE]")
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": '{"items": [{"id": 0, "text": "Hello world"}]}'}],
                          "response_format": {"type": "json_object"}}))
ok(st == 200 and json.loads(r["choices"][0]["message"]["content"])["items"][0]["text"] == "từ từ", "JSON có khuôn → structured_output")
n0 = len(calls())
st, r = asyncio.run(post({"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]}))
ok(st == 400 and len(calls()) == n0, "model ngoài danh sách → 400, không chạy tiến trình (TM-20)")
mode("nologin")
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "x"}]}))
ok(st == 401 and "đăng nhập" in r["error"]["message"], "CLI chưa đăng nhập → 401 kèm cách đăng nhập")
mode("crash")
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "x"}]}))
ok(st == 502 and "boom" in r["error"]["message"], "CLI chết giữa chừng → 502 kèm dòng lỗi")
mode("sleep")
old, CC.TIMEOUT = CC.TIMEOUT, 2
t = time.time()
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "x"}]}))
CC.TIMEOUT = old
time.sleep(0.5)
ok(st == 504 and time.time() - t < 15 and not calls_dirs(), "quá giờ → 504, tiến trình bị giết, thư mục tạm xoá (TM-22)")
mode("spawn")
CC.TIMEOUT = 2
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "x"}]}))
CC.TIMEOUT = old
time.sleep(0.5)
ok(st == 504 and not alive(int((TMP / "child.pid").read_text())), "quá giờ → giết cả tiến trình con của claude (F4)")
mode("flood")
(TMP / "child.pid").unlink(missing_ok=True)
CC.TIMEOUT = 2
t = time.time()
st, r = asyncio.run(post({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "x"}]}))
took = time.time() - t
CC.TIMEOUT = old
time.sleep(0.5)
pid = int((TMP / "child.pid").read_text()) if (TMP / "child.pid").exists() else 0
ok(st == 504 and took < 10 and pid and not alive(pid) and not calls_dirs(),
   f"claude in sự kiện dồn dập vẫn dừng đúng hạn chót cứng ({took:.1f} s với hạn 2 s), giết cả tiến trình con (TM-22, F4)")
reset()

print("3. Tính năng của app chạy bằng Claude Code (claude giả)")
cfg = {"base_url": CC.BASE, "model": "claude-haiku-5-5", "glossary_auto": False, "translator": "llm",
       "google_fallback": False, "chat_base_url": CC.BASE, "chat_model": "claude-haiku-5-5"}
with A.test_client() as c:
    s = c.get("/api/claude/status").json()
    ok(s["installed"] and s["logged_in"] and s["version"] == "9.9.9" and "claude-opus-5-5" in s["models"], "/api/claude/status")
    r = c.post("/api/models", json={"base_url": CC.BASE, "api_key": ""}).json()
    ids = [m["id"] for m in r.get("models", [])]
    ok("claude-sonnet-5-5" in ids and any(m.get("vision") for m in r.get("models", [])), "danh sách model của Claude Code, đọc được ảnh")
    with open(ROOT / "data/gqa.pdf", "rb") as f:
        did = c.post("/api/upload", files={"file": ("g.pdf", f, "application/pdf")}).json()["id"]
    r = c.post(f"/api/{did}/translate/0?span=1", json=cfg)
    pg = r.json()["pages"][0]
    D = A.DOCS[did]
    ok(r.status_code == 200 and pg.get("translated", 0) > 3 and all("từ" in v or not any(ch.isalpha() for ch in v) for v in D["trans"][0].values()),
       f"dịch trang qua Claude Code ({pg.get('translated')} đoạn)")
    r = c.post(f"/api/{did}/chat", json={"cfg": cfg, "question": "Tóm tắt?", "page": 0})
    ok(r.status_code == 200 and "Xin chào từ Claude" in r.text, "trò chuyện (stream) qua Claude Code")
    ok(A.pdf_input(A.Config(base_url=CC.BASE, model="claude-sonnet-5-5")) == "native", "chế độ “Nguyên PDF” bật cho Claude Code")

print("4. Dựng sơ đồ bằng Claude: lệnh agent bị khoá (TM-19)")
a = A.G.claude_argv("claude", "/venv/bin/python")
allowed = a[a.index("--allowedTools") + 1:a.index("--permission-mode")]
gate = f"Bash(/venv/bin/python -I {A.G.GATE} "
ok({"--safe-mode", "--restricted", "--strict-mcp-config", "--no-session-persistence"} <= set(a)
   and a[a.index("--permission-mode") + 1] == "dontAsk" and a[a.index("--permission-prompts") + 1] == "none",
   "--safe-mode, --restricted, không MCP, không lưu phiên, dontAsk")
ok(all(not x.startswith("Bash") or x.startswith(gate) for x in allowed) and sum(x.startswith(gate) for x in allowed) == 4
   and "Bash" not in allowed and not any(x.startswith(("WebFetch", "WebSearch")) for x in allowed),
   "shell chỉ cho lệnh paper_graph, không mạng")

import pg_gate
for bad in (["read", "/etc/passwd"], ["read", "../paper.blocks.json"], ["check", "paper.graph.json", "--base", "git"],
            ["check", "paper.graph.json", "--blocks", "/x.json"], ["fmt", "/Users/x/data/keys.json"],
            ["figure", "paper.blocks.json", "../../etc"], ["view", "paper.graph.json"], ["read", "paper.blocks.json", "--pages", "1;ls"]):
    try:
        pg_gate.build_argv(bad)
        ok(False, f"cổng từ chối {bad}")
    except pg_gate.Refused:
        pass
ok(pg_gate.build_argv(["check", "./paper.graph.json", "--pdf", "src.pdf", "--base", "base.graph.json"])
   == ["check", "paper.graph.json", "--blocks", "paper.blocks.json", "--pdf", "src.pdf", "--base", "base.graph.json"]
   and pg_gate.build_argv(["figure", "paper.blocks.json", "p3-f1", "p1-b2"]) == ["figure", "paper.blocks.json", "p3-f1", "p1-b2", "--pdf", "src.pdf"],
   "cổng chỉ nhận đúng file của thư mục làm việc, tự thêm --blocks, --pdf (F2); từ chối 8 dạng lệnh lạ")
work = TMP / "gate-work"
(work / "paper_graph").mkdir(parents=True)
shutil.copy2(ROOT.parent / "paper-graph" / "papers" / "rag" / "rag.blocks.json", work / "paper.blocks.json")
marker = TMP / "PWNED"
evil = f"open({str(marker)!r}, 'w').write('x')\n"
(work / "paper_graph" / "__init__.py").write_text(evil)
(work / "paper_graph" / "__main__.py").write_text(evil)
(work / "paper_graph" / "cli.py").write_text(evil + "def main(a=None):\n    return 0\n")
subprocess.run([sys.executable, "-m", "paper_graph", "read", "paper.blocks.json"], cwd=work, capture_output=True, timeout=60)
attack = marker.exists()
marker.unlink(missing_ok=True)
r = subprocess.run([sys.executable, "-I", str(A.G.GATE), "read", "paper.blocks.json", "--pages", "1"], cwd=work,
                   capture_output=True, text=True, timeout=120, env=CC.child_env())
ok(attack and not marker.exists() and r.returncode == 0 and "Retrieval-Augmented" in r.stdout,
   "gói paper_graph giả trong thư mục làm việc chạy được qua `python -m` nhưng không qua cổng `-I` (F1)")
r = subprocess.run([sys.executable, "-I", str(A.G.GATE), "fmt", str(TMP / "keys.json")], cwd=work, capture_output=True, text=True)
ok(r.returncode == 2 and "từ chối" in r.stderr, "cổng từ chối fmt file ngoài thư mục (F2)")

print("4b. Dựng sơ đồ bằng Claude: kiểm theo bản gốc, ô khoá giữ nguyên (reviewer M1)")
RAG = ROOT.parent / "paper-graph" / "papers" / "rag"
gid = "aaaaaaaaaaaa"
gp = A.G.paths(gid)
gp.blocks.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(RAG / "rag.blocks.json", gp.blocks)
base_doc = json.loads((RAG / "rag.graph.json").read_text("utf-8"))
base_doc["paper"]["blocks"] = gp.blocks.name
gp.graph.write_text(json.dumps(base_doc, ensure_ascii=False), "utf-8")
before = gp.graph.read_bytes()
changed = json.loads(json.dumps(base_doc))
changed["nodes"][0]["label"] = "Nhãn agent viết lại đè ô đã khoá"
(TMP / "build_graph.json").write_text(json.dumps(changed, ensure_ascii=False), "utf-8")
mode("build-evil")
okk, name = asyncio.run(A.G.run_claude(gid, ROOT / "data" / "rag.pdf"))
ok(not okk and name == gp.draft.name and gp.graph.read_bytes() == before,
   "agent đổi ô khoá và ghi {} vào bản sao sơ đồ cũ → bản nháp, sơ đồ của bài không đổi")
(TMP / "build_graph.json").write_text(json.dumps(base_doc, ensure_ascii=False), "utf-8")
mode("build")
okk, name = asyncio.run(A.G.run_claude(gid, ROOT / "data" / "rag.pdf"))
ok(okk and name == gp.graph.name and gp.graph.with_name("paper.graph.old.json").exists()
   and not [d for d in CC.WORK.iterdir() if d.name.startswith("graph-")],
   "sơ đồ giữ ô khoá → đạt, bản cũ sang paper.graph.old.json, thư mục làm việc đã xoá")
mode("flood")
(TMP / "child.pid").unlink(missing_ok=True)
old, A.G.CLAUDE_TIMEOUT = A.G.CLAUDE_TIMEOUT, 2
before, n0 = gp.graph.read_bytes(), len(A.G.JOB.get("log", []))
A.G.JOB.setdefault("log", [])
t = time.time()
try:
    asyncio.run(A.G.run_claude(gid, ROOT / "data" / "rag.pdf"))
    err = ""
except RuntimeError as e:
    err = str(e)
took = time.time() - t
A.G.CLAUDE_TIMEOUT = old
time.sleep(0.5)
pid = int((TMP / "child.pid").read_text()) if (TMP / "child.pid").exists() else 0
ok("chưa dựng xong" in err and 2 <= took < 10 and len(A.G.JOB["log"]) > n0 and pid and not alive(pid)
   and not [d for d in CC.WORK.iterdir() if d.name.startswith("graph-")] and gp.graph.read_bytes() == before,
   f"agent in sự kiện dồn dập vẫn dừng đúng hạn chót cứng ({took:.1f} s với hạn 2 s), tiến trình con bị giết, thư mục "
   "làm việc xoá, sơ đồ của bài không đổi (TM-22, F4)")
reset()

print("5. Dọn rác (TM-23)")
CC.WORK.mkdir(parents=True, exist_ok=True)
(CC.WORK / "call-cu").mkdir()
(CC.WORK / "call-dang-chay").mkdir()
CC.busy.add("call-dang-chay")
home = TMP / "home"
proj = home / ".claude" / "projects"
(proj / (CC._slug(CC.WORK) + "-call-cu-work")).mkdir(parents=True)
(proj / "-Users-ai-do-project-khac").mkdir(parents=True)
(CC.WORK / "call-ab_cd").mkdir()
CC.busy.add("call-ab_cd")
tmproot = CC.WORK / ".cctmp" / f"claude-{os.getuid()}"   # CLAUDE_CODE_TMPDIR của tiến trình con
old_tmp = tmproot / (CC._slug(CC.WORK) + "-call-cu-work")
live_tmp = tmproot / (CC._slug(CC.WORK / "call-ab_cd") + "-work")
for d in (old_tmp / "sess" / "scratchpad", live_tmp / "sess"):
    d.mkdir(parents=True, exist_ok=True)
old_home = pathlib.Path.home
pathlib.Path.home = classmethod(lambda cls: home)
try:
    r = CC.cleanup(max_age=0)
finally:
    pathlib.Path.home = old_home
CC.busy.discard("call-dang-chay")
CC.busy.discard("call-ab_cd")
ok(not old_tmp.exists() and live_tmp.exists(), "dọn cây thư mục tạm của claude -p trong $TMPDIR, giữ của lượt đang chạy (F5)")
shutil.rmtree(live_tmp, ignore_errors=True)
ok(not (CC.WORK / "call-cu").exists() and (CC.WORK / "call-dang-chay").exists() and (CC.WORK / ".cctmp").exists(),
   "xoá thư mục làm việc cũ, giữ thư mục đang chạy và thư mục tạm chung")
ok(CC.child_env()["CLAUDE_CODE_TMPDIR"] == str(CC.WORK / ".cctmp"), "thư mục tạm của Claude Code đặt trong WORK (reviewer L1)")
ok(r["session_dirs"] == 2 and (proj / "-Users-ai-do-project-khac").exists(), "chỉ xoá phiên thuộc thư mục làm việc của app")

print("\n" + ("TẤT CẢ ĐẠT" if not FAIL else f"{len(FAIL)} MỤC TRƯỢT"))
sys.exit(1 if FAIL else 0)
