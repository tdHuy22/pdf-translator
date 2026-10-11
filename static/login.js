// Trang đăng nhập (auth.py). Theo giao diện sáng/tối người dùng đã chọn trong app (localStorage.theme).
(() => {
  try { const t = localStorage.theme; if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch {}
  const $ = id => document.getElementById(id), msg = t => { $("msg").textContent = t; };

  fetch("/api/login").then(r => r.json()).then(s => {
    if (s.logged_in) location.replace("/");
    else if (!s.password_set) {
      $("pw").disabled = $("go").disabled = true;
      $("msg").innerHTML = "Chưa đặt mật khẩu. Chạy <code>.venv/bin/python auth.py --set-password</code> trên máy chạy app rồi tải lại trang.";
    }
  }).catch(() => {});

  $("form").addEventListener("submit", async e => {
    e.preventDefault();
    $("go").disabled = true;
    msg("");
    try {
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ password: $("pw").value }) });
      if (r.ok) { location.replace("/"); return; }
      msg((await r.json().catch(() => ({}))).detail || `Lỗi ${r.status}`);
      $("pw").select();
    } catch (err) {
      msg("Không kết nối được tới app: " + err.message);
    }
    $("go").disabled = false;
  });
})();
