// ================================================================ Hiển thị câu trả lời của LLM
// Markdown (có bảng) + công thức KaTeX + lọc HTML. Dùng cho khung trò chuyện, và sau này cho phần ghi chú.
//
// Thứ tự xử lý quan trọng, làm sai là hỏng:
//   1. Giấu code (``` và `…`) đi trước — nếu không, `$` trong code bị hiểu thành công thức.
//   2. Rút công thức ra chỗ giữ, vì Markdown ăn mất dấu `\` và `_` bên trong LaTeX ("x_1" thành "x1" in nghiêng).
//   3. Trả code lại rồi mới cho Markdown chạy (để code vẫn được định dạng như thường).
//   4. Dựng công thức bằng KaTeX, rồi LỌC HTML một lần cuối cho cả hai.
// Câu trả lời của LLM là dữ liệu từ bên ngoài, không phải mã của mình → luôn qua DOMPurify trước khi gắn vào trang.
//
// Bọc trong một hàm: file này nạp chung phạm vi toàn cục với app.js (script thường, không phải module), trùng một
// tên `const` thôi là app.js báo SyntaxError và KHÔNG chạy gì cả. Chỉ đưa ra ngoài renderMarkdown và paintInto.
(() => {
// Dấu ` đứng sau "\" là chữ thường với Markdown, không mở code → không coi là code ở đây (khớp cách marked hiểu)
const CODE = [
  /(?<!\\)```[\s\S]*?```/g,          // khối code (mở mà chưa đóng thì đã được cân bằng trước đó)
  /(?<!\\)(`+)(?:[^`]|(?!\1)`)+\1/g, // code giữa dòng
];
// Bốn kiểu dấu model hay dùng. $…$ giữa dòng phải chặt tay: "$5 và $10" không phải công thức.
const MATH = [
  [/\$\$([\s\S]+?)\$\$/g, true],
  [/\\\[([\s\S]+?)\\\]/g, true],
  [/\\\(([\s\S]+?)\\\)/g, false],
  [/(?<![\\$\w])\$(?!\s)((?:[^$\n]|\\\$)+?)(?<![\s\\])\$(?![\w$])/g, false],
];
// Model hay viết "$ \Psi $" (có cách trong dấu). Mẫu chặt ở trên bỏ qua kiểu này, nên dò thêm một lượt lỏng —
// nhưng chỉ nhận khi bên trong RÕ là LaTeX (lệnh \…, chỉ số _ ^, ngoặc {}), để "$5 và $10" vẫn là chữ thường.
const LOOSE = /(?<![\\$\w])\$(\s*(?:[^$\n]|\\\$)+?\s*)\$(?![\w$])/g;
const looksTex = s => /\\[A-Za-z]|[_^{}]/.test(s);

const holder = (kind, i) => `${kind}${i}`;

/** Chữ đang chảy dở: đóng tạm khối code và $$ còn mở, để đang gõ nửa công thức thì không nhấp nháy cả đoạn.
 *  `open` = đã phải tự đóng một $$ → công thức cuối cùng còn đang gõ dở. */
function balance(text) {
  if ((text.match(/```/g) || []).length % 2) text += "\n```";
  const open = (text.match(/\$\$/g) || []).length % 2 === 1;
  // model vừa gõ nửa dấu đóng ("…V$"): chỉ thêm một "$" cho đủ, không thì thừa một "$" lẻ loi ở cuối
  return { text: open ? text + (text.endsWith("$") ? "$" : "$$") : text, open };
}

// Mỗi công thức chỉ dựng bằng KaTeX MỘT lần: chữ đang chảy thì bong bóng được vẽ lại ~12 lần/giây, và mỗi lần
// dựng lại đủ mọi công thức trong đó. Nhớ kết quả thì vẽ công thức ở mọi khung hình mà không tốn gì — không phải
// chọn giữa "vẽ thưa cho đỡ nặng" (công thức nhấp nháy giữa chữ thô và chữ đã dựng) và "vẽ mau cho mượt" (giật).
const KCACHE = new Map();
function tex2html(tex, display) {
  const key = (display ? "D" : "I") + tex;
  let html = KCACHE.get(key);
  if (html === undefined) {
    try {
      html = window.katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false,
                                                strict: "ignore", output: "html" });
    } catch {                                                // LaTeX hỏng: giữ nguyên chữ chứ không nuốt mất câu
      html = rawMath(tex, display);
    }
    if (KCACHE.size > 1000) KCACHE.clear();
    KCACHE.set(key, html);
  }
  return html;
}
const rawMath = (tex, display) => esc(display ? `$$${tex}$$` : `$${tex}$`);

/** `open`: balance() đã tự đóng một $$ ở cuối → công thức $$ nào chạm đúng cuối chuỗi là công thức đang gõ dở.
 *  Phải nhận ra theo VỊ TRÍ: danh sách công thức xếp theo thứ tự các mẫu dấu ($$ trước, $ sau), không theo chữ. */
function extract(text, store, open) {
  for (const re of CODE) text = text.replace(re, m => (store.code.push(m), holder("C", store.code.length - 1)));
  for (const [re, display] of MATH) {
    text = text.replace(re, (m, tex, at, all) => {
      store.math.push({ tex, display, partial: open && display && at + m.length === all.length });
      return holder("M", store.math.length - 1);
    });
  }
  text = text.replace(LOOSE, (m, tex) => {
    if (!looksTex(tex)) return m;
    store.math.push({ tex: tex.trim(), display: false, partial: false });
    return holder("M", store.math.length - 1);
  });
  // HTML thô trong câu trả lời không bao giờ thành thẻ thật: "<" ngoài code và công thức hiện ra đúng là chữ "<".
  // Chỉ lọc bằng DOMPurify là chưa đủ, vì phải giữ thuộc tính style cho KaTeX, mà style="background:url(…)" cũng
  // làm trình duyệt tự tải một địa chỉ bên ngoài. Code thì không cần thoát: Markdown tự thoát chữ trong code.
  text = text.replace(/</g, "&lt;");
  // trả code lại NGAY: Markdown vẫn phải nhìn thấy code để định dạng, còn công thức thì không
  return text.replace(/C(\d+)/g, (m, i) => store.code[+i]);
}

/** Markdown + công thức → HTML đã lọc. */
function renderMarkdown(text) {
  const { text: src, open } = balance(String(text ?? ""));
  if (!window.marked || !window.DOMPurify) {                 // chưa tải được static/vendor/ → hiện chữ thô, vẫn đọc được
    const pre = document.createElement("pre");
    pre.className = "plain";
    pre.textContent = src;
    return pre.outerHTML;
  }
  const store = { code: [], math: [] };
  setup();
  let html = window.marked.parse(extract(src, store, open), { gfm: true, breaks: true, mangle: false, headerIds: false });
  html = html.replace(/M(\d+)/g, (m, i) => {
    const { tex, display, partial } = store.math[+i];
    // $$ đang gõ dở: hiện chữ thô, đừng để KaTeX báo lỗi đỏ nhấp nháy theo từng chữ
    if (!window.katex || partial) return rawMath(tex, display);
    return tex2html(tex, display);
  });
  return window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true, mathMl: true, svg: true },
                                           FORBID_TAGS: [...NO_FETCH, ...NO_UI], FORBID_ATTR: ["srcset", "poster", "action", "formaction"] });
}
// Thẻ mà trình duyệt TỰ tải về ngay khi hiện ra, không cần ai bấm. Câu trả lời LLM không cần ảnh, còn giữ lại
// thì một bài báo có câu kiểu "hãy chèn ảnh https://…/?q=<tóm tắt cuộc trò chuyện>" là đủ để nội dung bị gửi ra
// ngoài khi câu trả lời hiện lên — Markdown ![](…) tự sinh ra <img> dù HTML thô đã bị thoát ở trên.
// Link <a> thì vẫn giữ: phải bấm mới đi, và người dùng thấy trước địa chỉ.
const NO_FETCH = ["img", "image", "picture", "source", "video", "audio", "track", "iframe", "embed", "object",
                  "link", "use", "feimage"];
// Thẻ dựng được giao diện giả (ô mật khẩu "phiên hết hạn" gửi về trang lạ) hay CSS cho cả trang (<svg><style>).
const NO_UI = ["style", "form", "input", "button", "textarea", "select", "option", "dialog", "meta", "base"];
// style vẫn phải giữ cho KaTeX (cao, lề, độ lệch từng ký hiệu), nhưng không được tải gì (url(), image-set) hay thoát
// khỏi khung trả lời để trùm lên app (position fixed/absolute/sticky). CSS của trang (static/index.html) còn giới hạn
// khung bằng `contain: paint`, và CSP (auth.py) chặn tải từ nơi khác — ba lớp, lớp nào hỏng thì còn lớp khác.
const BAD_STYLE = /url\s*\(|image-set|@import|expression\s*\(|position\s*:\s*(fixed|absolute|sticky)/i;

let ready = false;
/** Cài một lần (thư viện nạp trước file này): Markdown KHÔNG xuất HTML thô, và bộ lọc thuộc tính style ở trên.
 *  `<` ngoài code đã bị thoát trong extract(), nhưng code tự nhận dạng (`…`) không khớp hẳn cách marked hiểu: "\`<div …>`"
 *  là code với regex ở trên, còn với marked là dấu ` thường + thẻ HTML thật. Thoát ở renderer thì không còn khe hở đó. */
function setup() {
  if (ready) return;
  ready = true;
  // Thẻ HTML giữa dòng → chữ thường. Chỉ thoát ở renderer là chưa đủ: thẻ <pre>/<code>/<kbd>/<script> giữa dòng bật
  // chế độ "raw" của marked, chữ phía sau nó KHÔNG được thoát nữa (reviewer tái hiện 11/10/2026). Nhận "<" ở đây trước
  // khi tokenizer gốc kịp chạy thì chế độ đó không bao giờ bật.
  window.marked.use({
    renderer: { html: h => esc(typeof h === "string" ? h : (h?.text ?? "")) },
    tokenizer: { tag(src) {
      if (src[0] !== "<") return false;
      const raw = (/^<[^<>]*>/.exec(src) || ["<"])[0];
      return { type: "text", raw, text: esc(raw) };
    } },
  });
  window.DOMPurify.addHook("uponSanitizeAttribute", (node, data) => {
    // dò trên CSS đã bỏ chú thích và giải mã escape: "position:/**/fixed", "\75rl(", "f\ixed" với trình duyệt vẫn là
    // position:fixed, url(, fixed
    const css = data.attrName !== "style" ? "" : data.attrValue.replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\\([0-9a-f]{1,6})\s?/gi, (m, x) => String.fromCodePoint(Math.min(parseInt(x, 16), 0x10ffff) || 0xfffd))
      .replace(/\\/g, "");
    if (BAD_STYLE.test(css)) data.keepAttr = false;
  });
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** Vẽ vào một thẻ, có tiết lưu ~80ms (server gửi về từng vài chữ một, vẽ lại mỗi mẩu là thừa).
 *  `done` = chữ đã chảy xong → luôn vẽ lần cuối, bỏ qua tiết lưu. */
function paintInto(el, text, done = false) {
  const now = performance.now();
  if (!done && now - (el._rdAt || 0) < 80) return;
  el.innerHTML = renderMarkdown(text);
  el._rdAt = now;
}

// ---------------- Chữ đang chảy: vẽ lại từng khối, không vẽ lại cả câu trả lời
// Vẽ lại cả câu trả lời mỗi lần có chữ mới thì càng dài càng chậm (Markdown + KaTeX chạy lại từ đầu), và bôi đen
// để chép giữa chừng là mất vùng chọn. Chia câu trả lời thành các khối cấp cao (ngăn bởi dòng trống, không cắt
// trong ``` hay $$): khối đã xong giữ nguyên trên trang, chỉ khối cuối (đang chảy) được vẽ lại. Xong hẳn thì vẽ lại
// cả câu trả lời một lần — vài cấu trúc Markdown trải qua dòng trống (danh sách có dòng trống giữa các mục) chỉ
// đúng hẳn khi dựng cả bài.
function splitBlocks(text) {
  const out = [];
  let cur = [], fence = false, math = false;
  for (const line of text.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    else if (!fence && (line.match(/\$\$/g) || []).length % 2) math = !math;
    if (!fence && !math && !line.trim()) {
      if (cur.length) { out.push(cur.join("\n")); cur = []; }
      continue;
    }
    cur.push(line);
  }
  if (cur.length) out.push(cur.join("\n"));
  return out;
}

/** Vẽ chữ đang chảy vào `el`, tiết lưu ~50ms. `done`: vẽ lần cuối cả câu trả lời (và tô màu code, nút chép). */
function streamInto(el, text, done = false) {
  if (done) {
    el.innerHTML = renderMarkdown(text);
    el._blocks = null;
    enhance(el);
    return;
  }
  const now = performance.now();
  if (now - (el._rdAt || 0) < 50) return;
  el._rdAt = now;
  const blocks = splitBlocks(text);
  if (!el._blocks) { el.innerHTML = ""; el._blocks = []; }
  const have = el._blocks;
  let i = 0;
  while (i < blocks.length - 1 && i < have.length && have[i].src === blocks[i]) i++;
  while (have.length > i) have.pop().node.remove();
  for (; i < blocks.length; i++) {
    const node = document.createElement("div");
    node.className = "mdb";
    node.innerHTML = renderMarkdown(blocks[i]);
    if (i < blocks.length - 1) enhance(node);          // khối đã xong: tô màu code luôn
    el.appendChild(node);
    have.push({ src: blocks[i], node });
  }
}

// ---------------- Khối code: tô màu (highlight.js), tên ngôn ngữ và nút chép
if (window.hljs) window.hljs.configure({ ignoreUnescapedHTML: true });
function enhance(root) {
  for (const pre of root.querySelectorAll("pre:not(.plain)")) {
    const code = pre.querySelector("code");
    if (!code || pre.dataset.done) continue;
    pre.dataset.done = "1";
    if (window.hljs) try { window.hljs.highlightElement(code); } catch {}
    const lang = (code.className.match(/language-([\w+#.-]+)/) || [])[1] || "";
    const bar = document.createElement("div");
    bar.className = "codebar";
    bar.innerHTML = `<span>${esc(lang)}</span><button type="button">📋 Chép</button>`;
    const btn = bar.querySelector("button");
    btn.onclick = async () => {
      try { await navigator.clipboard.writeText(code.textContent); btn.textContent = "✓ Đã chép"; }
      catch { btn.textContent = "✗"; }
      setTimeout(() => { btn.textContent = "📋 Chép"; }, 1500);
    };
    pre.prepend(bar);
  }
}

window.renderMarkdown = renderMarkdown;
window.paintInto = paintInto;
window.streamInto = streamInto;
})();
