"""Claude Code làm nhà cung cấp model: mọi lượt gọi LLM của app tới `BASE` (địa chỉ giả, không ra mạng) được
`Transport` đổi thành một tiến trình `claude -p` trên máy chạy app, dùng đăng nhập gói Claude của chủ máy.

Request kiểu OpenAI (`/chat/completions`: system, lịch sử, chữ, ảnh `image_url`, PDF `file`, `response_format`,
`stream`) → `claude -p --input-format stream-json` (một tin nhắn người dùng, ảnh và PDF thành khối `image`,
`document`), `--json-schema` khi request đòi JSON có khuôn; câu trả lời đổi lại thành JSON hay SSE kiểu OpenAI, nên
code gọi LLM sẵn có (dịch, thuật ngữ, trò chuyện, căn vệt tô, xem ảnh, OCR bằng VLM, công thức) không phải sửa.

Chốt chặn ở `.agent/threat-model.md` mục 2026-10-23 "Claude Code" (TM-19 … TM-23):
- không công cụ nào (`--tools ""`), `--safe-mode` bỏ CLAUDE.md, hook, plugin, MCP, skill của người dùng;
- tham số là danh sách (không shell), model trong danh sách cho phép, system prompt qua file, chữ qua stdin;
- bỏ biến môi trường làm `claude` gọi API trả tiền thay vì gói;
- tối đa `SLOTS` tiến trình, có trần thời gian, client ngắt thì giết tiến trình;
- `--no-session-persistence`, thư mục làm việc tạm xoá sau mỗi lượt, `cleanup()` dọn phần còn sót."""
import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://claude-code.local/v1"
HOST = "claude-code.local"
MODELS = {   # id → tên hiện trong giao diện
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-fable-5-1": "Claude Fable 5.1",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}
CONTEXT = 200_000
SLOTS = max(1, int(os.environ.get("CLAUDE_CODE_SLOTS", "4")))
TIMEOUT = float(os.environ.get("CLAUDE_CODE_TIMEOUT", "1200"))   # giây mỗi lượt
WORK = Path(__file__).resolve().parent / "data" / "claude-work"
# Môi trường tiến trình con: danh sách cho phép (TM-21, differential-review F3). Không truyền ANTHROPIC_*, AWS_*,
# CLAUDE_CODE_USE_BEDROCK/VERTEX (tính tiền API thay vì gói), proxy, NODE_OPTIONS, CLAUDE_CONFIG_DIR, hay secret khác
# của service. CLAUDE_CODE_OAUTH_TOKEN là token của `claude setup-token` (đăng nhập gói cho máy không có trình duyệt).
KEEP_ENV = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TERM",
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
            "CLAUDE_CODE_OAUTH_TOKEN"}
busy: set[str] = set()   # tên thư mục làm việc đang dùng (lượt gọi, lượt dựng sơ đồ): cleanup() không xoá
_EFFORT = {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high"}


def is_claude(url: str | None) -> bool:
    try:
        return httpx.URL(url or "").host == HOST
    except (httpx.InvalidURL, TypeError):
        return False


def binary() -> str | None:
    """Đường dẫn `claude`: biến CLAUDE_BIN, PATH, hay chỗ bộ cài chính thức đặt (service systemd có PATH ngắn)."""
    for c in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"), str(Path.home() / ".local/bin/claude")):
        if c and os.access(c, os.X_OK):
            return c
    return None


def child_env() -> dict:
    """Môi trường theo danh sách cho phép; thư mục tạm của Claude Code đặt trong WORK (mặc định nó ghi vào
    /tmp/claude-<uid>/<slug>/, đo trên Mac 23/10/2026: reviewer L1) để cleanup() dọn được."""
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV}
    env["CLAUDE_CODE_TMPDIR"] = str(cc_tmp())
    return env


def cc_tmp() -> Path:
    d = WORK / ".cctmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def drain_tail(stream, keep: int = 4096) -> bytes:
    """Đọc hết stderr theo khúc, chỉ giữ `keep` byte cuối (tiến trình in nhiều cũng không ăn RAM, không nghẽn ống)."""
    tail = b""
    while chunk := await stream.read(65536):
        tail = (tail + chunk)[-keep:]
    return tail


async def kill_tree(proc) -> None:
    """Giết tiến trình và mọi tiến trình con của nó (chạy với start_new_session=True nên cùng một nhóm)."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pass


async def status() -> dict:
    """Đã cài chưa, bản nào, đã đăng nhập chưa (`claude auth status`, không tốn lượt gọi model)."""
    exe = binary()
    if not exe:
        return {"installed": False, "logged_in": False, "version": "", "detail": "Chưa cài Claude Code trên máy chạy app"}
    out = {"installed": True, "logged_in": False, "version": "", "detail": ""}
    try:
        v = await _run_simple([exe, "--version"])
        out["version"] = v.strip().split()[0] if v.strip() else ""
        st = json.loads(await _run_simple([exe, "auth", "status"]) or "{}")
        out["logged_in"] = bool(st.get("loggedIn"))
        out["detail"] = st.get("authMethod", "")
    except (OSError, ValueError, asyncio.TimeoutError) as e:
        out["detail"] = f"{type(e).__name__}: {e}"[:300]
    return out


async def _run_simple(argv: list[str], timeout: float = 20) -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    p = await asyncio.create_subprocess_exec(*argv, cwd=WORK, env=child_env(), stdin=asyncio.subprocess.DEVNULL,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise
    return out.decode("utf-8", "replace")


# ---------------------------------------------------------------- đổi request kiểu OpenAI → claude -p
class BadRequest(ValueError):
    pass


def _data_url(url: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"data:([\w.+/-]+);base64,(.+)", url or "", re.S)
    return (m.group(1), m.group(2)) if m else None


def _blocks(content) -> list[dict]:
    """Nội dung một tin nhắn kiểu OpenAI → các khối kiểu Anthropic (text, image, document)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            out.append({"type": "text", "text": str(part.get("text", ""))})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if d := _data_url(url):
                out.append({"type": "image", "source": {"type": "base64", "media_type": d[0], "data": d[1]}})
        elif kind == "file":   # PDF kiểu OpenRouter: {"file": {"filename", "file_data": "data:application/pdf;base64,…"}}
            d = _data_url((part.get("file") or {}).get("file_data", ""))
            if d and d[0] == "application/pdf":
                out.append({"type": "document", "source": {"type": "base64", "media_type": d[0], "data": d[1]}})
    return out


def _text_of(content) -> str:
    return "\n".join(b["text"] for b in _blocks(content) if b["type"] == "text")


def convert(body: dict) -> dict:
    """Request kiểu OpenAI → {system, message (stream-json), schema, model, effort, stream}."""
    model = str(body.get("model") or "")
    if model not in MODELS:   # TM-20
        raise BadRequest(f"model {model[:80]!r} không có trong danh sách Claude Code ({', '.join(MODELS)})")
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise BadRequest("thiếu messages")
    system = "\n\n".join(_text_of(m.get("content")) for m in msgs if isinstance(m, dict) and m.get("role") in ("system", "developer"))
    turns = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not turns:
        raise BadRequest("thiếu tin nhắn của người dùng")
    blocks: list[dict] = []
    if len(turns) > 1:   # lịch sử: chép thành chữ, ảnh của các lượt trước giữ nguyên khối
        lines = ["Lịch sử cuộc trò chuyện trước tin nhắn cuối (để hiểu ngữ cảnh):"]
        for m in turns[:-1]:
            lines.append(("NGƯỜI DÙNG: " if m["role"] == "user" else "TRỢ LÝ: ") + _text_of(m.get("content")))
            blocks += [b for b in _blocks(m.get("content")) if b["type"] != "text"]
        blocks.insert(0, {"type": "text", "text": "\n\n".join(lines)})
        blocks.append({"type": "text", "text": "TIN NHẮN CUỐI CỦA NGƯỜI DÙNG:"})
    last = turns[-1]
    blocks += _blocks(last.get("content"))
    if last["role"] == "assistant":   # mồi câu trả lời (ít gặp): coi như yêu cầu viết tiếp
        blocks = [*blocks[:-1], {"type": "text", "text": "Viết tiếp câu trả lời bắt đầu bằng: " + _text_of(last.get("content"))}]
    if not any(b["type"] == "text" and b["text"].strip() for b in blocks) and not any(b["type"] != "text" for b in blocks):
        raise BadRequest("tin nhắn rỗng")
    schema = None
    rf = body.get("response_format") or {}
    if isinstance(rf, dict) and rf.get("type") == "json_schema":
        schema = (rf.get("json_schema") or {}).get("schema") or {"type": "object"}
    elif isinstance(rf, dict) and rf.get("type") == "json_object":
        schema = {"type": "object"}
    effort = body.get("reasoning_effort")
    if isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort", effort)
        if body["reasoning"].get("enabled") is False:
            effort = "none"
    return {"system": system, "message": {"type": "user", "message": {"role": "user", "content": blocks}},
            "schema": schema, "model": model, "effort": _EFFORT.get(str(effort or "")), "stream": bool(body.get("stream"))}


def argv(exe: str, job: dict, system_file: Path | None) -> list[str]:
    # --restricted: bỏ qua settings của người dùng (env, apiKeyHelper có thể trỏ tới API trả tiền: reviewer C2)
    a = [exe, "-p", "--safe-mode", "--restricted", "--tools", "", "--no-session-persistence", "--model", job["model"],
         "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
         "--permission-mode", "dontAsk"]
    if system_file is not None:
        a += ["--system-prompt-file", str(system_file)]
    else:   # không system prompt của request: vẫn thay prompt mặc định của Claude Code (dạy viết code, dùng công cụ)
        a += ["--system-prompt", "Bạn là trợ lý trả lời đúng yêu cầu, không có công cụ."]
    if job["schema"] is not None:
        a += ["--json-schema", json.dumps(job["schema"], ensure_ascii=False)]
    if job["effort"]:
        a += ["--effort", job["effort"]]
    return a


# ---------------------------------------------------------------- chạy tiến trình
_sems: dict = {}


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _sems:
        _sems[loop] = asyncio.Semaphore(SLOTS)
    return _sems[loop]


class CliError(Exception):
    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


async def events(job: dict):
    """Chạy một lượt `claude -p`, lần lượt trả các sự kiện stream-json. Giết tiến trình khi xong, lỗi, hết giờ hay khi
    người gọi thôi đọc (TM-22); xoá thư mục làm việc tạm (TM-23)."""
    exe = binary()
    if not exe:
        raise CliError("Chưa cài Claude Code trên máy chạy app (https://code.claude.com)", 503)
    async with _sem():
        WORK.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="call-", dir=WORK))
        busy.add(tmp.name)
        proc = None
        try:
            sysf = None
            if job["system"]:
                sysf = tmp / "system.txt"
                sysf.write_text(job["system"], "utf-8")
            work = tmp / "work"
            work.mkdir()
            proc = await asyncio.create_subprocess_exec(
                *argv(exe, job, sysf), cwd=work, env=child_env(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 << 20, start_new_session=True)

            async def feed():
                proc.stdin.write((json.dumps(job["message"], ensure_ascii=False) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            err_task = asyncio.create_task(drain_tail(proc.stderr))
            side = [asyncio.create_task(feed()), err_task]
            deadline = time.monotonic() + TIMEOUT
            got_result = False
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise CliError(f"Claude Code quá {int(TIMEOUT)} giây chưa xong", 504)
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), left)
                except asyncio.TimeoutError:
                    raise CliError(f"Claude Code quá {int(TIMEOUT)} giây chưa xong", 504) from None
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    got_result = True
                yield ev
            await proc.wait()
            await asyncio.gather(*side, return_exceptions=True)
            if not got_result:
                tail = (err_task.result() if err_task.done() and not err_task.cancelled() and not err_task.exception() else b""
                        ).decode("utf-8", "replace").strip().splitlines()[-3:]
                raise CliError("Claude Code dừng không trả kết quả (mã " + str(proc.returncode) + "): "
                               + " ".join(tail)[:400])
        finally:
            if proc is not None:
                await kill_tree(proc)
            busy.discard(tmp.name)
            shutil.rmtree(tmp, ignore_errors=True)


def _result_error(ev: dict) -> CliError | None:
    if ev.get("subtype") == "success" and not ev.get("is_error"):
        return None
    text = str(ev.get("result") or ev.get("subtype") or "lỗi không rõ")
    if "login" in text.lower() or "logged in" in text.lower():
        return CliError("Claude Code trên máy chạy app chưa đăng nhập: chạy `claude` rồi `/login` (hay `claude setup-token`) "
                        "trên máy đó", 401)
    if ev.get("api_error_status") == 429 or "limit" in text.lower():
        return CliError(f"Claude Code: đã chạm hạn mức của gói ({text[:200]})", 429)
    return CliError(f"Claude Code báo lỗi: {text[:400]}")


def _usage(ev: dict) -> dict:
    u = ev.get("usage") or {}
    inp = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"prompt_tokens": inp, "completion_tokens": int(u.get("output_tokens") or 0),
            "total_tokens": inp + int(u.get("output_tokens") or 0)}


def _final_text(ev: dict) -> str:
    if ev.get("structured_output") is not None:
        return json.dumps(ev["structured_output"], ensure_ascii=False)
    return str(ev.get("result") or "")


async def complete(job: dict) -> dict:
    """Câu trả lời đầy đủ kiểu OpenAI `chat.completion`."""
    final = None
    async for ev in events(job):
        if ev.get("type") == "result":
            final = ev
    if err := _result_error(final):
        raise err
    return {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": job["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": _final_text(final)},
                         "finish_reason": "stop"}],
            "usage": _usage(final)}


def _chunk(model: str, cid: str, delta: dict, finish=None, usage=None) -> bytes:
    doc = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        doc["usage"] = usage
    return b"data: " + json.dumps(doc, ensure_ascii=False).encode("utf-8") + b"\n\n"


class _SSE(httpx.AsyncByteStream):
    """Thân SSE kiểu OpenAI, sinh dần theo sự kiện stream-json. Người đọc đóng sớm thì generator bị đóng → tiến trình
    bị giết trong `events()`."""

    def __init__(self, job: dict):
        self.job = job
        self.gen = None

    async def __aiter__(self):
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        model, sent = self.job["model"], False
        self.gen = events(self.job)
        try:
            yield _chunk(model, cid, {"role": "assistant", "content": ""})
            async for ev in self.gen:
                if ev.get("type") == "stream_event":
                    d = (ev.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text") and self.job["schema"] is None:
                        sent = True
                        yield _chunk(model, cid, {"content": d["text"]})
                elif ev.get("type") == "result":
                    if err := _result_error(ev):
                        doc = {"error": {"message": str(err), "code": err.status}}
                        yield b"data: " + json.dumps(doc, ensure_ascii=False).encode("utf-8") + b"\n\n"
                        return
                    if not sent:   # JSON có khuôn, hay CLI không gửi từng mẩu: gửi cả câu trả lời một lần
                        yield _chunk(model, cid, {"content": _final_text(ev)})
                    yield _chunk(model, cid, {}, "stop", _usage(ev))
            yield b"data: [DONE]\n\n"
        except CliError as e:
            yield b"data: " + json.dumps({"error": {"message": str(e), "code": e.status}}, ensure_ascii=False).encode() + b"\n\n"
        finally:
            await self.gen.aclose()

    async def aclose(self):
        if self.gen is not None:
            await self.gen.aclose()


def _err(status: int, msg: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": msg, "type": "claude_code", "code": status}})


class Transport(httpx.AsyncBaseTransport):
    """Gắn vào httpx.AsyncClient qua `mounts={"http://claude-code.local": Transport()}` (xem app.llm_client)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if request.method == "GET" and path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": k, "name": v, "context_length": CONTEXT, "owned_by": "claude-code",
                 "architecture": {"input_modalities": ["text", "image", "file"]},
                 "pricing": {"prompt": "0", "completion": "0"}} for k, v in MODELS.items()]})
        if request.method != "POST" or path != "/v1/chat/completions":
            return _err(404, "Claude Code chỉ có /v1/models và /v1/chat/completions")
        try:
            body = json.loads(await request.aread())
            job = convert(body if isinstance(body, dict) else {})
        except (ValueError, BadRequest) as e:
            return _err(400, str(e))
        if job["stream"]:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_SSE(job))
        try:
            return httpx.Response(200, json=await complete(job))
        except CliError as e:
            return _err(e.status, str(e))


# ---------------------------------------------------------------- dọn rác (TM-23)
def _slug(path: Path) -> str:
    """Tên thư mục Claude Code đặt cho phiên của một thư mục làm việc trong ~/.claude/projects/."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def cleanup(max_age: float = 3600, keep: set[str] | None = None) -> dict:
    """Xoá thư mục làm việc tạm cũ hơn `max_age` giây (trừ thư mục đang dùng), phiên Claude Code mà thư mục làm việc
    của app có thể đã để lại trong ~/.claude/projects/ (lỡ chạy thiếu `--no-session-persistence`, bản CLI cũ), và cây
    thư mục rỗng mỗi lượt `claude -p` để lại trong `$TMPDIR/claude-<uid>/<slug>/` (đo 23/10/2026). Chỉ đụng tên bắt đầu
    bằng slug của `WORK`: phiên của các project khác không bị chạm. Gọi từ vòng sự kiện (route async) hay lúc khởi động,
    nơi `busy` được sửa, nên không đua với lượt gọi đang mở thư mục."""
    keep = set(busy) | (keep or set())
    keep_slugs = [_slug(WORK / k) for k in keep]   # mkdtemp có "_", slug thì không: so theo slug (F5)
    removed_dirs, removed_sessions = 0, 0
    now = time.time()
    if WORK.exists():
        for d in WORK.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name not in keep and now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
                removed_dirs += 1
    prefix = _slug(WORK)
    tmp_roots = {WORK / ".cctmp" / f"claude-{os.getuid()}", Path("/tmp") / f"claude-{os.getuid()}",
                 Path(tempfile.gettempdir()) / f"claude-{os.getuid()}"}   # chỗ hiện tại, và chỗ mặc định của bản cũ
    for root in (Path.home() / ".claude" / "projects", *tmp_roots):
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and d.name.startswith(prefix) and not any(d.name.startswith(k) for k in keep_slugs):
                    shutil.rmtree(d, ignore_errors=True)
                    removed_sessions += 1
    return {"work_dirs": removed_dirs, "session_dirs": removed_sessions}
