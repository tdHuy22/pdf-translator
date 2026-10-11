"""Kiểm các chốt chặn bảo mật (auth.py, .agent/threat-model.md TM-1…TM-5) bằng request thật qua TestClient:
đăng nhập, chặn mặc định mọi route, giới hạn dò mật khẩu, cookie, Origin, Host, header bảo mật, file 0600, giới hạn
upload và zoom. Dùng thư mục tạm — không đụng data/auth.json, data/keys.json hay tài liệu thật.

    .venv/bin/python scripts/test_security.py      # ~10 giây

In ✓/✗ cho từng mục, thoát mã 1 nếu có mục sai."""
import atexit
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import threading
import time

import pymupdf
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app as A  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="vietpaper-security-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
A.store.ROOT = TMP / "docs"
A.KEYS_FILE, A._keys = TMP / "keys.json", {}             # không đụng key thật
A.USER_TERMS_FILE = TMP / "user_terms.json"
A.PROVIDERS_FILE = TMP / "providers.json"
A.PROVIDERS_V2 = TMP / "providers.v2"                   # …kể cả file đánh dấu đã chuyển providers
A.MODEL_META_FILE = TMP / "model_meta.json"
A.load_keys = lambda: None
AUTH = A.auth.AUTH
AUTH.use_file(TMP / "auth.json")                         # không đụng mật khẩu thật
PW = "mat-khau-thu-123"                                  # giá trị thử, chỉ sống trong thư mục tạm
FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ SAI ") + msg)
    if not cond:
        FAIL.append(msg)


def client(base="http://localhost"):
    return TestClient(A.app, base_url=base, follow_redirects=False)


def pdf_bytes(pages: int) -> bytes:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"Page {i + 1}: attention is all you need.")
    return d.tobytes()


def login(c) -> "httpx.Response":  # noqa: F821
    AUTH.fails.clear()
    return c.post("/api/login", json={"password": PW})


def mode(p: pathlib.Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


with client() as anon:
    print("1. Chưa đặt mật khẩu: từ chối hết, không mở toang (TM-5)")
    r = anon.get("/api/docs")
    ok(r.status_code == 503 and "set-password" in r.json()["detail"], f"/api/docs → {r.status_code} kèm cách đặt mật khẩu")
    r = anon.get("/", headers={"accept": "text/html"})
    ok(r.status_code == 303 and r.headers["location"] == "/login.html", f"mở trang chủ → {r.status_code} về trang đăng nhập")
    ok(anon.get("/api/login").json() == {"password_set": False, "logged_in": False}, "/api/login báo chưa đặt mật khẩu")
    ok(anon.post("/api/login", json={"password": PW}).status_code == 503, "đăng nhập khi chưa có mật khẩu → 503")
    with A.test_client() as local:
        ok(local.get("/api/docs").status_code == 200, "A.test_client() (phiên trong process) vẫn dùng được, không cần mật khẩu")

    AUTH.set_password(PW)
    print("2. Chặn mặc định: mọi route khi chưa đăng nhập (TM-1)")
    fill = {"doc_id": "abcdef012345", "n": "0", "kind": "original", "pid": "x", "sid": "x", "note_id": "x", "mid": "x"}
    leaks = []
    for route in A.app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: fill.get(m[1], "x"), route.path)
        for method in route.methods - {"HEAD"}:
            code = anon.request(method, path).status_code
            want = 200 if route.path in A.auth.PUBLIC else 401
            if code != want and not (route.path == "/api/login" and method == "POST" and code == 422):
                leaks.append(f"{method} {route.path} → {code}")
    n_routes = sum(len(r.methods - {"HEAD"}) for r in A.app.routes if isinstance(r, APIRoute))
    ok(not leaks, f"{n_routes} cặp method/route: chưa đăng nhập đều nhận 401 (trừ API đăng nhập)" + (f" — lọt: {leaks}" if leaks else ""))
    for path, accept, want in [("/app.js", "*/*", 401), ("/index.html", "text/html", 303), ("/render-test.html", "text/html", 303),
                               ("/openapi.json", "*/*", 401), ("/docs", "text/html", 303),
                               ("/login.html", "text/html", 200), ("/login.js", "*/*", 200)]:
        code = anon.get(path, headers={"accept": accept}).status_code
        ok(code == want, f"GET {path} (chưa đăng nhập) → {code}, cần {want}")

    print("3. Dò mật khẩu bị chặn (TM-2)")
    AUTH.fails.clear()
    codes = [anon.post("/api/login", json={"password": f"sai-{i}"}).status_code for i in range(5)]
    r = anon.post("/api/login", json={"password": PW})
    ok(codes == [401] * 5 and r.status_code == 429 and "retry-after" in r.headers,
       f"5 lần sai → 401, lần 6 (dù đúng mật khẩu) → {r.status_code} kèm Retry-After")
    ok(A.auth.COOKIE not in anon.cookies, "lần bị chặn không cấp phiên")
    t = time.perf_counter()
    AUTH.fails.clear()
    anon.post("/api/login", json={"password": "sai"})
    ok(time.perf_counter() - t > 0.02, "kiểm mật khẩu tốn thời gian thật (scrypt), không so chuỗi trơn")

with client() as c:
    print("4. Đăng nhập, cookie phiên (TM-3)")
    r = login(c)
    sc = r.headers.get("set-cookie", "")
    ok(r.status_code == 200 and "HttpOnly" in sc and "SameSite=Lax" in sc and "Max-Age=2592000" in sc,
       "đúng mật khẩu → cookie HttpOnly, SameSite=Lax, giữ 30 ngày")
    ok("Secure" not in sc, "qua http (localhost) thì không đặt Secure")
    with client("https://localhost") as s:
        ok("Secure" in login(s).headers.get("set-cookie", ""), "qua HTTPS (tailscale serve) thì cookie có Secure")
    ok(c.get("/api/docs").status_code == 200, "có cookie → /api/docs 200")
    token = c.cookies[A.auth.COOKIE]
    data = json.loads((TMP / "auth.json").read_text())
    ok(token not in json.dumps(data) and PW not in json.dumps(data), "auth.json không chứa token hay mật khẩu (chỉ băm)")
    ok(A.auth.Auth(TMP / "auth.json").check(token)[0], "phiên sống qua lần khởi động lại (đọc lại từ file)")
    ok(c.get("/api/docs", cookies={A.auth.COOKIE: token[:-2] + "xx"}).status_code == 401, "token sửa vài ký tự → 401")

    key = A.auth._digest(token)
    AUTH.data["sessions"][key]["seen"] -= 2 * A.auth.TOUCH_EVERY
    r = c.get("/api/docs")
    ok("Max-Age=2592000" in r.headers.get("set-cookie", ""), "dùng lại sau hơn một ngày → gia hạn, gửi lại cookie 30 ngày")
    AUTH.data["sessions"][key]["exp"] = time.time() - 1
    ok(c.get("/api/docs").status_code == 401, "phiên quá hạn → 401")

    login(c)
    token = c.cookies[A.auth.COOKIE]
    r = c.post("/api/logout")
    ok(r.status_code == 200 and "Max-Age=0" in r.headers.get("set-cookie", ""), "đăng xuất xoá cookie")
    ok(c.get("/api/docs", cookies={A.auth.COOKIE: token}).status_code == 401, "token cũ sau khi đăng xuất → 401")

    login(c)
    token = c.cookies[A.auth.COOKIE]
    AUTH.set_password(PW + "-moi")
    ok(c.get("/api/docs").status_code == 401, "đổi mật khẩu → mọi phiên cũ bị thu hồi")
    AUTH.set_password(PW)
    login(c)

    # Lệnh --set-password (tiến trình khác) ghi đúng lúc app đang kiểm mật khẩu cũ (scrypt chạy ~0,07 s): app không được
    # cấp phiên theo mật khẩu cũ, và không được ghi đè file bằng bản cũ (mất mật khẩu mới, sống lại phiên đã thu hồi)
    real_scrypt, slow = A.auth._scrypt, threading.Event()
    A.auth._scrypt = lambda *a: (time.sleep(0.4) if slow.is_set() else None) or real_scrypt(*a)
    AUTH.fails.clear()
    slow.set()
    out = {}
    t = threading.Thread(target=lambda: out.update(r=AUTH.login(PW)))
    t.start()
    time.sleep(0.15)
    slow.clear()
    A.auth.Auth(TMP / "auth.json").set_password(PW + "-cli")      # như một tiến trình khác chạy lệnh
    t.join()
    A.auth._scrypt = real_scrypt
    after = A.auth.Auth(TMP / "auth.json")
    ok(out["r"] == (None, 0) and after._verify(PW + "-cli", after.data.get("password")) and not after.data["sessions"],
       "đổi mật khẩu bằng lệnh giữa lúc đang đăng nhập: giữ mật khẩu mới, không cấp phiên theo mật khẩu cũ")
    AUTH.set_password(PW)
    login(c)
    token = c.cookies[A.auth.COOKIE]
    st = (TMP / "auth.json").stat()
    A.auth.Auth(TMP / "auth.json").set_password(PW + "-tick")    # tiến trình khác ghi…
    os.utime(TMP / "auth.json", ns=(st.st_atime_ns, st.st_mtime_ns))   # …trong cùng một tick đồng hồ (mtime trùng)
    ok(not AUTH.check(token)[0], "đổi mật khẩu trong cùng tick đồng hồ (mtime không đổi) vẫn thu hồi phiên cũ")
    AUTH.set_password(PW)
    login(c)

    print("5. Request từ trang khác bị chặn (TM-4)")
    body = {"slot": "http://x/v1", "key": "sk-test-0000000000000000"}
    cases = [({"origin": "http://evil.example"}, 403, "Origin trang lạ"),
             ({"origin": "http://localhost:9999"}, 403, "Origin cùng máy khác cổng (vd. dịch vụ khác trên cùng tên ts.net)"),
             ({"origin": "null"}, 403, "Origin null (iframe sandbox, file://)"),
             ({"sec-fetch-site": "cross-site"}, 403, "Sec-Fetch-Site: cross-site"),
             ({"sec-fetch-site": "same-site"}, 403, "Sec-Fetch-Site: same-site"),
             ({"referer": "http://evil.example/x"}, 403, "chỉ có Referer trang lạ"),
             ({"origin": "https://localhost"}, 403, "Origin cùng host nhưng khác scheme (https ↔ http)"),
             ({"origin": "http://localhost"}, 200, "Origin đúng trang app"),
             ({"sec-fetch-site": "same-origin"}, 200, "Sec-Fetch-Site: same-origin"),
             ({}, 200, "không có Origin (script, curl) — vẫn cần cookie đúng")]
    for headers, want, name in cases:
        code = c.post("/api/keys", json=body, headers=headers).status_code
        ok(code == want, f"POST /api/keys, {name} → {code}")
    code = anon.post("/api/login", json={"password": PW}, headers={"origin": "http://evil.example"}).status_code
    ok(code == 403, f"đăng nhập từ trang lạ (login CSRF) → {code}")
    ok(c.delete("/api/docs/abcdef012345", headers={"origin": "http://evil.example"}).status_code == 403, "DELETE từ trang lạ → 403")

    with client("https://localhost") as s:
        login(s)
        ok(s.post("/api/keys", json=body, headers={"origin": "https://localhost"}).status_code == 200,
           "qua HTTPS: Origin https://… đúng trang app → 200")
    r = c.get("/api/docs", headers={"tailscale-funnel-request": "?1"})
    ok(r.status_code == 403, f"request đi qua Tailscale Funnel (Internet) → {r.status_code}, kể cả khi có cookie")

    print("6. Host lạ bị chặn (DNS rebinding)")
    ok(c.get("/api/docs", headers={"host": "evil.example"}).status_code == 400, "Host evil.example → 400")
    ok(c.get("/api/docs", headers={"host": "127.0.0.1:8000"}).status_code == 200, "Host 127.0.0.1:8000 → 200")

    print("7. Header bảo mật trên mọi phản hồi")
    for name, r in [("200", c.get("/api/docs")), ("401", anon.get("/api/docs")), ("303", anon.get("/", headers={"accept": "text/html"})),
                    ("403", c.post("/api/keys", json=body, headers={"origin": "http://evil.example"})), ("file tĩnh", c.get("/app.js"))]:
        csp = r.headers.get("content-security-policy", "")
        script = next((d for d in csp.split("; ") if d.startswith("script-src")), "")
        ok(script == "script-src 'self'" and "frame-ancestors 'none'" in csp and "img-src 'self' data: blob:" in csp
           and r.headers.get("x-content-type-options") == "nosniff", f"{name}: CSP chặt (không unsafe-inline cho script), nosniff")

    print("8. Key và file bí mật (S7, S8, TM-5)")
    r = c.post("/api/keys", json={"slot": "http://x/v1/", "key": "sk-or-v1-abcdefghijklmnop"})
    ok(r.json()["keys"].get("http://x/v1") == "sk-o…mnop" and "abcdefghijkl" not in r.text, "POST /api/keys chỉ trả dạng che")
    ok(A._keys.get("http://x/v1") == "sk-or-v1-abcdefghijklmnop", "server nhớ key theo base_url")
    ok(mode(A.KEYS_FILE) == 0o600 and mode(TMP / "auth.json") == 0o600, "keys.json, auth.json quyền 0600")
    ok(not list(TMP.glob(".*.tmp")), "không sót file tạm sau khi ghi")
    for path in ["/data/auth.json", "/../data/auth.json", "/%2e%2e/data/keys.json", "/static/../data/keys.json"]:
        r = c.get(path)
        ok(r.status_code in (400, 404) and "scrypt" not in r.text and "sk-or" not in r.text, f"GET {path} → {r.status_code}, không lộ file")

    print("9. Giới hạn tài nguyên (S6)")
    small = pdf_bytes(2)
    A.BODY_LIMITS["/api/upload"] = len(small) // 2
    r = c.post("/api/upload", files={"file": ("a.pdf", small, "application/pdf")})
    ok(r.status_code == 413, f"file vượt giới hạn cỡ → {r.status_code} (chặn trước khi nhận body)")
    # không khai Content-Length (chunked; HTTP/2 qua proxy): vẫn đếm và dừng khi vượt, không đọc hết vào bộ nhớ
    def chunks(data, n=4096):
        for i in range(0, len(data), n):
            yield data[i:i + n]
    boundary = "vp-test-boundary"
    form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"d.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode() + small + f"\r\n--{boundary}--\r\n".encode()
    hdr = {"content-type": f"multipart/form-data; boundary={boundary}"}
    r = c.post("/api/upload", content=chunks(form), headers=hdr)
    ok(r.status_code == 413 and "content-length" not in r.request.headers, f"upload chunked vượt giới hạn → {r.status_code}")
    A.BODY_LIMITS["/api/upload"] = A.MAX_UPLOAD
    r = c.post("/api/upload", content=chunks(form), headers=hdr)
    ok(r.status_code == 200, f"upload chunked dưới giới hạn vẫn nhận (qua proxy không có Content-Length) → {r.status_code}")
    r = anon.post("/api/login", content=b'{"password": "' + b"x" * 10000 + b'"}', headers={"content-type": "application/json"})
    ok(r.status_code == 413, f"body đăng nhập quá cỡ (người chưa đăng nhập) → {r.status_code}")
    A.MAX_PAGES, keep = 1, A.MAX_PAGES
    r = c.post("/api/upload", files={"file": ("b.pdf", pdf_bytes(3), "application/pdf")})
    ok(r.status_code == 413 and "tối đa 1" in r.json()["detail"], f"PDF quá số trang → {r.status_code}")
    A.MAX_PAGES = keep
    r = c.post("/api/upload", files={"file": ("c.pdf", small, "application/pdf")})
    ok(r.status_code == 200 and r.json()["pages"] == 2, "PDF thường vẫn tải lên được")
    did = r.json()["id"]
    w = lambda z: pymupdf.Pixmap(c.get(f"/api/{did}/page/0/original.png?zoom={z}").content).width
    ok(w(40) == w(4) and w(0.01) == w(0.5) and w(2) < w(4) and w("nan") == w(2) and w("inf") == w(2),
       "zoom bị kẹp trong 0,5–4 (nan, inf → mặc định)")
    ok(A.refused_param({"max_tokens": 8192}, 400, "max_tokens is too large") == "max_tokens",
       "server từ chối max_tokens của trò chuyện → bỏ tham số rồi hỏi lại")

    print("10. Lệnh đặt mật khẩu không có đường web (TM-5)")
    ok(not [r.path for r in A.app.routes if isinstance(r, APIRoute) and "password" in r.path],
       "không có route nào đặt/đổi mật khẩu")

print(f"\n{'Tất cả đạt' if not FAIL else f'{len(FAIL)} mục SAI'}")
sys.exit(1 if FAIL else 0)
