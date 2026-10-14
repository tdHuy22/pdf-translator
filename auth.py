"""Đăng nhập cho VietPaper: một người dùng, một mật khẩu, phiên lưu phía server.

App chạy trên server nhà, vào qua `tailscale serve` (HTTPS, chỉ máy trong tailnet). Mọi request đi qua Guard — middleware
ASGI CHẶN MẶC ĐỊNH: chưa đăng nhập thì chỉ mở được trang đăng nhập, nên route thêm sau này cũng không lọt. Các chốt
chặn ở đây ứng với TM-1…TM-5 trong .agent/threat-model.md.

    .venv/bin/python auth.py --set-password     # đặt / đổi mật khẩu (đổi là thu hồi mọi phiên)

Mật khẩu chỉ đặt được bằng lệnh trên máy chạy app, không có trang đặt mật khẩu trên web: chưa đặt thì app từ chối mọi
request (503) chứ không mở toang cho người đầu tiên tới đặt.
"""
import collections
import contextlib
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import PlainTextResponse, RedirectResponse

FILE = Path(__file__).resolve().parent / "data" / "auth.json"
COOKIE = "vp_session"
SESSION_DAYS = 30
TOUCH_EVERY = 24 * 3600        # gia hạn phiên (và gửi lại cookie) tối đa mỗi ngày một lần, đỡ ghi file mỗi request
MAX_FAILS, FAIL_WINDOW = 5, 60  # sai 5 lần trong 60 giây → khoá đăng nhập tới khi lần sai cũ nhất ra khỏi cửa sổ
MIN_LEN = 8
# scrypt n=2^15, r=8: 32 MiB RAM, ~0,07 s mỗi lần thử (đo trên M2) — file băm có lộ thì dò offline vẫn tốn kém
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
NO_PASSWORD = "Chưa đặt mật khẩu: chạy `.venv/bin/python auth.py --set-password` trên máy chạy app"

# Mở khi chưa đăng nhập: trang đăng nhập và API của nó, cùng manifest + icon để "Thêm vào Màn hình chính" (trình duyệt
# tải manifest KHÔNG kèm cookie; trong đó không có gì riêng tư)
PUBLIC = {"/login.html", "/login.js", "/api/login",
          "/manifest.json", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# CSP: chỉ chạy script của chính app (không script nội tuyến, không onclick="…"), không tải ảnh/font/kết nối nơi khác.
# Đây là lớp chặn thứ hai cho câu trả lời LLM (đã lọc ở static/render.js): CSS lọt lưới như background:url(…) cũng không
# gửi được gì ra ngoài. style-src phải cho 'unsafe-inline' vì KaTeX và giao diện dùng thuộc tính style.
CSP = "; ".join([
    "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
    "font-src 'self' data:", "connect-src 'self'", "form-action 'self'", "frame-ancestors 'none'",
    "base-uri 'none'", "object-src 'none'",
])
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


def write_private(path: Path, text: str) -> None:
    """Ghi file chỉ chủ máy đọc được (0600) và thay trong một bước (file tạm + os.replace): lỗi giữa chừng không để lại
    file hỏng, và không lúc nào file mới nằm trên đĩa với quyền mặc định."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp: quyền 0600 ngay khi tạo, tên riêng cho mỗi lần ghi (hai luồng cùng ghi không xoá file tạm của nhau)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _digest(token: str) -> str:
    """Server chỉ giữ băm của token: file auth.json có lộ cũng không dùng được để vào."""
    return hashlib.sha256(token.encode()).hexdigest()


def _scrypt(password: str, salt: bytes, p: dict) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=p["n"], r=p["r"], p=p["p"], maxmem=64 << 20, dklen=32)


class Auth:
    """Mật khẩu (băm scrypt) + bảng phiên, lưu trong một file JSON. Một process uvicorn nên giữ trong bộ nhớ là đủ;
    đọc lại file khi nó đổi (đặt mật khẩu bằng lệnh trong lúc app đang chạy). Mọi lần ghi đều đọc lại file rồi sửa
    rồi ghi trong khoá liên tiến trình (_file_lock): không ghi đè bản mới của tiến trình khác bằng dữ liệu cũ."""

    def __init__(self, path: Path = FILE):
        self.lock = threading.Lock()                # đăng nhập chạy tuần tự: không bắn song song để vượt giới hạn
        self.local: set[str] = set()                # phiên chỉ sống trong process (script dùng TestClient), không ghi file
        self.fails: collections.deque[float] = collections.deque()
        self.use_file(path)

    def use_file(self, path: Path) -> None:
        self.path, self.sig, self.data = Path(path), None, {}
        self.reload()

    @contextlib.contextmanager
    def _file_lock(self):
        """Khoá quanh đọc–sửa–ghi auth.json, chung cho mọi tiến trình (app, lệnh --set-password) và mọi luồng. Chỉ giữ
        trong lúc đọc và ghi file (vài ms), không giữ trong lúc chạy scrypt."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)            # nhả khi đóng file
            yield

    def _sig(self) -> tuple[int, int, int] | None:
        """Dấu nhận biết file đã đổi. Có cả inode: os.replace luôn tạo inode mới, nên hai lần ghi rơi vào cùng một tick
        đồng hồ (mtime trùng) vẫn phân biệt được."""
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def reload(self, force: bool = False) -> dict:
        """Đọc lại file nếu nó đã đổi, trả bản dữ liệu hiện hành. Hàm ghi sửa đúng bản trả về rồi save(bản đó) — không
        dùng self.data, vì luồng khác có thể vừa thay self.data bằng bản đọc lại."""
        sig = self._sig()
        if sig == self.sig and self.data and not force:
            return self.data
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        self.sig, self.data = sig, data
        return data

    def save(self, data: dict) -> None:
        write_private(self.path, json.dumps(data, indent=1))
        self.sig, self.data = self._sig(), data

    def has_password(self) -> bool:
        return bool(self.reload().get("password"))

    def set_password(self, password: str) -> None:
        """Đặt mật khẩu mới và thu hồi mọi phiên cũ (kể cả phiên trên máy bị mất / nghi lộ)."""
        salt = os.urandom(16)
        record = {**SCRYPT, "salt": salt.hex(), "hash": _scrypt(password, salt, SCRYPT).hex()}
        with self._file_lock():
            self.local.clear()
            self.save({"password": record, "sessions": {}})

    @staticmethod
    def _verify(password: str, record: dict | None) -> bool:
        p = record or {}
        try:
            want, got = bytes.fromhex(p["hash"]), _scrypt(password, bytes.fromhex(p["salt"]), p)
        except (KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(want, got)

    def _wait(self) -> int:
        """Số giây còn bị khoá. Đếm chung, không theo máy: app chỉ có một người dùng và chỉ máy trong tailnet tới được,
        nên chặn dò mật khẩu (kể cả dò từ nhiều máy) đáng hơn rủi ro một máy lạ làm chủ máy bị khoá tạm một phút."""
        now = time.monotonic()
        while self.fails and now - self.fails[0] > FAIL_WINDOW:
            self.fails.popleft()
        return int(FAIL_WINDOW - (now - self.fails[0])) + 1 if len(self.fails) >= MAX_FAILS else 0

    def login(self, password: str) -> tuple[str | None, int]:
        """(token phiên mới, 0) nếu đúng mật khẩu; (None, 0) nếu sai; (None, số giây phải chờ) nếu đang bị khoá."""
        with self.lock:
            record = self.reload().get("password")
            if wait := self._wait():
                return None, wait
            if not self._verify(password, record):          # scrypt ~0,07 s, ngoài khoá file
                self.fails.append(time.monotonic())
                return None, 0
            with self._file_lock():
                data = self.reload(force=True)
                if data.get("password") != record:           # mật khẩu vừa bị đổi bằng lệnh trong lúc đang kiểm
                    return None, 0
                self.fails.clear()
                return self._add_session(data), 0

    def new_session(self, persist: bool = True) -> str:
        if not persist:
            token = secrets.token_urlsafe(32)
            self.local.add(_digest(token))
            return token
        with self._file_lock():
            return self._add_session(self.reload(force=True))

    def _add_session(self, data: dict) -> str:
        """Gọi trong _file_lock, với bản vừa đọc lại từ file."""
        token = secrets.token_urlsafe(32)
        now, sessions = time.time(), data["sessions"]
        for k in [k for k, s in sessions.items() if not isinstance(s, dict) or s.get("exp", 0) < now]:
            del sessions[k]                                  # dọn phiên hết hạn
        sessions[_digest(token)] = {"exp": now + SESSION_DAYS * 86400, "seen": now}
        self.save(data)
        return token

    def check(self, token: str | None) -> tuple[bool, bool]:
        """(phiên còn hiệu lực, vừa được gia hạn → gửi lại cookie để trình duyệt cũng giữ thêm 30 ngày)."""
        if not token:
            return False, False
        key = _digest(token)
        if key in self.local:
            return True, False
        s, now = self.reload()["sessions"].get(key), time.time()
        if not isinstance(s, dict) or s.get("exp", 0) < now:
            return False, False
        if now - s.get("seen", 0) < TOUCH_EVERY:
            return True, False
        with self._file_lock():
            data = self.reload(force=True)
            s = data["sessions"].get(key)
            if not isinstance(s, dict) or s.get("exp", 0) < now:   # vừa bị thu hồi (đổi mật khẩu, đăng xuất)
                return False, False
            s.update(exp=now + SESSION_DAYS * 86400, seen=now)
            self.save(data)
        return True, True

    def logout(self, token: str | None) -> None:
        if not token:
            return
        key = _digest(token)
        self.local.discard(key)
        with self._file_lock():
            data = self.reload(force=True)
            if data["sessions"].pop(key, None) is not None:
                self.save(data)


AUTH = Auth()


def cookie_header(token: str, secure: bool, max_age: int = SESSION_DAYS * 86400) -> str:
    # SameSite=Lax chứ không Strict: Strict làm mở link app từ ứng dụng khác luôn bị đòi đăng nhập lại; phần chống
    # CSRF do kiểm Origin trong Guard lo. Secure khi đi qua HTTPS (tailscale serve báo X-Forwarded-Proto).
    return "; ".join([f"{COOKIE}={token}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
                     + (["Secure"] if secure else []))


def same_origin(h: Headers, scheme: str) -> bool:
    """Request đổi dữ liệu phải đến từ chính trang của app: Origin khớp ĐỦ scheme://host:cổng (cùng máy khác cổng —
    vd. dịch vụ khác trên cùng tên ts.net — là trang khác: cookie thì gửi chung theo host, chỉ Origin phân biệt được
    cổng). `scheme` là của request đã qua proxy (uvicorn đọc X-Forwarded-Proto của tailscale serve ở 127.0.0.1).
    Trình duyệt luôn gửi Origin hoặc Sec-Fetch-Site với POST/PUT/DELETE; không có cả hai (và Referer) là script, curl —
    loại này vẫn phải có cookie đúng mới qua được."""
    origin = h.get("origin")
    if origin is None:
        if (site := h.get("sec-fetch-site")) is not None:
            return site in ("same-origin", "none")
        if (origin := h.get("referer")) is None:
            return True
    u = urlsplit(origin)
    return bool(u.netloc) and (u.scheme.lower(), u.netloc.lower()) == (scheme, h.get("host", "").lower())


def allowed_hosts() -> list[str]:
    """Tên được phép trong header Host (chặn DNS rebinding: trang lạ trỏ tên miền của nó về IP app). Mặc định chỉ
    localhost; thêm tên máy qua biến môi trường ALLOWED_HOSTS (vd. <máy>.<tailnet>.ts.net), cách nhau dấu phẩy.
    HOST là một IP cụ thể (không phải 0.0.0.0) thì tự thêm IP đó."""
    hosts = ["localhost", "127.0.0.1"]
    hosts += [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    if (bind := os.environ.get("HOST", "")) and bind not in ("0.0.0.0", "::"):
        hosts.append(bind)
    return list(dict.fromkeys(hosts))


class Guard:
    """Middleware ASGI bọc gần ngoài cùng (trong TrustedHostMiddleware): kiểm Origin, đòi đăng nhập, giới hạn cỡ body,
    thêm header bảo mật cho MỌI phản hồi — kể cả file tĩnh và trang báo lỗi."""

    def __init__(self, app, auth: Auth, body_limits: dict[str, int] | None = None):
        self.app, self.auth = app, auth
        self.body_limits = body_limits if body_limits is not None else {}   # giữ tham chiếu: test đổi được giới hạn

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":                     # app không có websocket: đóng luôn, đừng để lọt qua cửa sau
            return await send({"type": "websocket.close", "code": 1008})
        h, method, path = Headers(scope=scope), scope["method"], scope["path"]
        token = cookie_parser(h.get("cookie", "")).get(COOKIE)
        extra: list[tuple[bytes, bytes]] = []

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), *SECURITY_HEADERS, *extra]}
            await send(msg)

        async def reply(resp):
            await resp(scope, receive, send_with_headers)

        if "tailscale-funnel-request" in h:   # lỡ bật Funnel cho cổng của app: không phục vụ người ngoài Internet
            return await reply(PlainTextResponse("App chỉ mở trong tailnet, không qua Funnel", 403))
        if method not in SAFE_METHODS and not same_origin(h, scope.get("scheme", "http")):
            return await reply(PlainTextResponse("Yêu cầu từ trang khác bị chặn", 403))
        ok, touched = self.auth.check(token)
        if not ok and path not in PUBLIC:
            api = path.startswith("/api/") or method not in SAFE_METHODS
            if not self.auth.has_password():
                return await reply(JSONResponse({"detail": NO_PASSWORD}, 503) if api
                                   else RedirectResponse("/login.html", 303))
            if api or "text/html" not in h.get("accept", ""):
                return await reply(JSONResponse({"detail": "Chưa đăng nhập"}, 401))
            return await reply(RedirectResponse("/login.html", 303))
        if (limit := self.body_limits.get(path)) and method not in SAFE_METHODS:
            too_big = f"Dữ liệu gửi lên quá lớn (tối đa {limit:,} byte)".replace(",", ".")
            size = h.get("content-length")
            if size is not None and (not size.isdigit() or int(size) > limit):
                return await reply(JSONResponse({"detail": too_big}, 413))
            if size is None:   # không khai cỡ (chunked, HTTP/2 qua proxy): đếm từng mẩu, vượt thì dừng nhận
                receive = limit_body(receive, limit, too_big)
        if touched and path != "/api/logout":              # đăng xuất thì đừng gửi kèm cookie gia hạn
            extra.append((b"set-cookie", cookie_header(token, scope.get("scheme") == "https").encode()))
        await self.app(scope, receive, send_with_headers)


def limit_body(receive, limit: int, detail: str):
    """Bọc `receive` của ASGI: body vượt `limit` byte thì ném HTTPException 413 ngay lúc đang nhận — FastAPI đang đọc
    body (form/JSON) nên lỗi về đúng người gọi, không phải đọc hết rồi mới biết."""
    seen = 0

    async def wrapped():
        nonlocal seen
        msg = await receive()
        if msg["type"] == "http.request":
            seen += len(msg.get("body", b""))
            if seen > limit:
                raise HTTPException(413, detail)
        return msg
    return wrapped


# ================================================================ API đăng nhập
router = APIRouter()


class LoginBody(BaseModel):
    password: str = Field(max_length=1024)


@router.get("/api/login")
def login_state(request: Request):
    """Cho trang đăng nhập biết: đã đặt mật khẩu chưa, và trình duyệt này đang có phiên chưa."""
    return {"password_set": AUTH.has_password(), "logged_in": AUTH.check(request.cookies.get(COOKIE))[0]}


@router.post("/api/login")
def login(body: LoginBody, request: Request):
    # route thường (không async): scrypt ~0,07 s chạy trong threadpool, không chặn các request khác
    if not AUTH.has_password():
        raise HTTPException(503, NO_PASSWORD)
    token, wait = AUTH.login(body.password)
    if wait:
        raise HTTPException(429, f"Sai quá nhiều lần, thử lại sau {wait} giây", headers={"Retry-After": str(wait)})
    if not token:
        raise HTTPException(401, "Sai mật khẩu")
    resp = JSONResponse({"ok": True})
    resp.headers.append("set-cookie", cookie_header(token, request.url.scheme == "https"))
    return resp


@router.post("/api/logout")
def logout(request: Request):
    AUTH.logout(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.headers.append("set-cookie", cookie_header("", request.url.scheme == "https", max_age=0))
    return resp


if __name__ == "__main__":
    if sys.argv[1:] != ["--set-password"]:
        sys.exit(__doc__)
    pw = getpass.getpass("Mật khẩu mới: ")
    if len(pw) < MIN_LEN:
        sys.exit(f"Mật khẩu phải dài ít nhất {MIN_LEN} ký tự")
    if getpass.getpass("Gõ lại: ") != pw:
        sys.exit("Hai lần gõ không khớp, chưa đổi gì")
    AUTH.set_password(pw)
    print(f"Đã lưu mật khẩu (băm scrypt) vào {FILE}, quyền 0600. Mọi phiên đăng nhập cũ đã bị thu hồi.")
