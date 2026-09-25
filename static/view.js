// ================================================================ Khung đọc: chế độ xem, thu phóng, kính lúp, bề rộng khung chat
// Phóng to NGAY trên khung đọc chính (không mở khung riêng chứa bản sao của trang): lớp chữ, tô vàng, ghi chú đều
// tìm trang bằng pageOf() — có hai bản sao của một trang là chúng bám nhầm bản. Lớp chữ tự co theo nhờ ResizeObserver
// của app.js, vệt tô lưu theo toạ độ PDF nên cũng tự khớp.
// Ảnh trang được vẽ lại nét hơn khi phóng to (server vẽ PNG theo `zoom`): mặc định ×2, tối đa ×4.
//
// Bọc trong một hàm như notes.js; dùng biến của app.js ($, doc, pageOf) và đưa ra ngoài: viewLayout, viewVisible.
(() => {
const Z_MIN = 0.4, Z_MAX = 4, CHAT_W = 380;
const load = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
let mode = load("view_mode", "both");        // "both" | "original" | "translated"
let zoom = Math.min(Z_MAX, Math.max(Z_MIN, parseFloat(load("view_zoom", "1")) || 1));

// ---------------- Bố cục: bề rộng mỗi cột = (chỗ trống ÷ số cột) × zoom
function chatWidth() {
  return document.body.classList.contains("chat") && innerWidth > 900 ? $("chat").offsetWidth : 0;
}
function layout() {
  const cols = $("cols"), n = mode === "both" ? 2 : 1;
  cols.classList.toggle("only-original", mode === "original");
  cols.classList.toggle("only-translated", mode === "translated");
  const vw = document.documentElement.clientWidth;
  document.documentElement.style.setProperty("--vw", vw + "px");
  const free = vw - chatWidth() - 24 - (n - 1) * 12;
  const w = Math.max(160, Math.floor(free / n * zoom));
  cols.style.setProperty("--colw", w + "px");
  // rộng hơn màn hình mà vẫn căn giữa thì phần tràn sang trái bị cắt, không cuộn tới được
  cols.style.justifyContent = w * n + (n - 1) * 12 > free + (n - 1) * 12 + 1 ? "start" : "center";
  cols.style.setProperty("--ncol", n);
  $("zoomPct").textContent = Math.round(zoom * 100) + "%";
  document.querySelectorAll("#viewbar [data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  sharpenSoon();
}

/** Trang (phần tử .page đang hiện) nằm ở giữa màn hình, và điểm giữa màn hình nằm ở đâu trên trang đó (0–1). */
function anchor() {
  const cx = (innerWidth - chatWidth()) / 2, cy = innerHeight / 2;
  let best = null, dist = Infinity;
  for (const el of document.querySelectorAll(".page")) {
    if (!el.offsetParent) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight) continue;
    const d = Math.abs((r.top + r.bottom) / 2 - cy) + Math.abs((r.left + r.right) / 2 - cx) / 4;
    if (d < dist) { dist = d; best = { el, fx: (cx - r.left) / r.width, fy: (cy - r.top) / r.height }; }
  }
  return best;
}
/** Đổi bố cục mà vẫn giữ nguyên chỗ đang đọc (phóng to quanh giữa màn hình, như trình xem PDF). */
function keepPlace(change) {
  const a = anchor();
  change();
  layout();
  if (!a || !a.el.offsetParent) return;
  const r = a.el.getBoundingClientRect(), cx = (innerWidth - chatWidth()) / 2, cy = innerHeight / 2;
  window.scrollBy(r.left + a.fx * r.width - cx, r.top + a.fy * r.height - cy);
}
function setZoom(z) {
  z = Math.min(Z_MAX, Math.max(Z_MIN, Math.round(z * 100) / 100));
  if (z === zoom) return;
  keepPlace(() => { zoom = z; save("view_zoom", z); });
}
function setMode(m) {
  // chuyển sang một cột: trang đang đọc ở bản bị ẩn → neo theo trang cùng số ở bản còn lại
  const a = anchor();
  mode = m; save("view_mode", m);
  layout();
  const p = a && +a.el.dataset.p, el = a && [pageOf(p, "original"), pageOf(p, "translated")].find(x => x?.offsetParent);
  if (el) {
    const r = el.getBoundingClientRect();
    window.scrollBy(0, r.top + a.fy * r.height - innerHeight / 2);
  }
}

$("viewbar").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.mode) setMode(b.dataset.mode);
  else if (b.id === "zoomIn") setZoom(zoom * 1.2);
  else if (b.id === "zoomOut") setZoom(zoom / 1.2);
  else if (b.id === "zoomFit" || b.id === "zoomPct") setZoom(1);
});
// ⌘/Ctrl + lăn chuột (và chụm hai ngón trên trackpad — trình duyệt báo thành wheel có ctrlKey) trên vùng trang
window.addEventListener("wheel", e => {
  if (!(e.ctrlKey || e.metaKey) || !e.target.closest?.("#cols")) return;
  e.preventDefault();
  setZoom(zoom * Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.004)));
}, { passive: false });
window.addEventListener("resize", () => layout());

// ---------------- Ảnh nét theo mức phóng
// Ảnh vẽ ở mức q (điểm ảnh / pt). Cần q ≈ bề rộng hiển thị × devicePixelRatio ÷ bề rộng trang (pt). Chỉ tăng, không
// giảm (ảnh đã nét thì thu nhỏ vẫn nét), và làm tròn lên 2/3/4 để không tải lại ảnh ở mỗi nấc phóng.
function qOf(img) { return parseFloat(new URL(img.src, location.href).searchParams.get("zoom")) || 2; }
function sharpen(img) {
  if (!img.naturalWidth || !img.clientWidth) return;
  const q = qOf(img), widthPt = img.naturalWidth / q;
  const need = Math.min(4, Math.ceil(img.clientWidth * (devicePixelRatio || 1) / widthPt - 0.15));
  if (need <= q) return;
  const u = new URL(img.src, location.href);
  u.searchParams.set("zoom", need);
  img.src = u.pathname + u.search;
}
let sharpTimer = null;
function sharpenSoon() {
  clearTimeout(sharpTimer);
  sharpTimer = setTimeout(() => {       // chỉ các trang đang ở gần màn hình; trang khác tự nét khi ảnh được nạp
    for (const img of document.querySelectorAll(".page img")) {
      const r = img.getBoundingClientRect();
      if (r.bottom > -innerHeight && r.top < 2 * innerHeight) sharpen(img);
    }
  }, 300);
}
$("cols").addEventListener("load", e => { if (e.target.tagName === "IMG") sharpen(e.target); }, true);
window.addEventListener("scroll", sharpenSoon, { passive: true });

// ---------------- Kính lúp: giữ ⌥ Option / Alt trong khi rê chuột trên trang
// Phóng ×2,5 từ ảnh đang hiện thì mờ (ở 100% ảnh chỉ vẽ ×2), nên kính lúp tải riêng bản ×4 của trang đó; trong lúc
// chờ thì soi tạm trên ảnh đang hiện. Bản ×4 đã tải được nhớ lại (trình duyệt cũng giữ trong bộ nhớ đệm).
const lens = $("lens"), LENS_K = 2.5, HI = new Map();       // url ảnh đang hiện → url bản ×4 đã tải xong
let mouse = null;
function hiRes(img) {
  const u = new URL(img.src, location.href);
  if (qOf(img) >= 4) return img.src;
  if (HI.has(img.src)) return HI.get(img.src) || img.src;  // "" = đang tải
  u.searchParams.set("zoom", 4);
  const hi = u.pathname + u.search, pre = new Image(), key = img.src;
  HI.set(key, "");
  pre.onload = () => { HI.set(key, hi); if (!lens.hidden && mouse) lensAt(mouse); };
  pre.onerror = () => HI.delete(key);
  pre.src = hi;
  return img.src;
}
function lensAt(ev) {
  const img = ev && document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".page")?.querySelector("img");
  if (!img || !img.naturalWidth) { lens.hidden = true; return; }
  const r = img.getBoundingClientRect(), R = lens.offsetWidth / 2 || 120;
  lens.hidden = false;
  lens.style.left = ev.clientX - R + "px";
  lens.style.top = ev.clientY - R + "px";
  lens.style.backgroundImage = `url("${hiRes(img)}")`;
  lens.style.backgroundSize = `${r.width * LENS_K}px ${r.height * LENS_K}px`;
  lens.style.backgroundPosition = `${R - (ev.clientX - r.left) * LENS_K}px ${R - (ev.clientY - r.top) * LENS_K}px`;
}
document.addEventListener("mousemove", e => {
  mouse = e;
  if (e.altKey) lensAt(e); else if (!lens.hidden) lens.hidden = true;
}, { passive: true });
document.addEventListener("keydown", e => { if (e.key === "Alt" && mouse) lensAt(mouse); });
document.addEventListener("keyup", e => { if (e.key === "Alt") lens.hidden = true; });
window.addEventListener("blur", () => { lens.hidden = true; });

// ---------------- Kéo rộng / thu hẹp khung chat
function setChatW(w, keep = true) {
  w = Math.round(Math.min(innerWidth * 0.7, Math.max(300, w)));
  document.documentElement.style.setProperty("--chatw", w + "px");
  if (keep) save("chat_w", w);
  layout();
}
setChatW(+load("chat_w", CHAT_W) || CHAT_W, false);
$("chatGrip").addEventListener("pointerdown", e => {
  e.preventDefault();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
  const move = ev => setChatW(innerWidth - ev.clientX);
  const up = () => { grip.removeEventListener("pointermove", move); document.body.classList.remove("resizing"); };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up, { once: true });
  grip.addEventListener("pointercancel", up, { once: true });
});
$("chatGrip").addEventListener("dblclick", () => setChatW(CHAT_W));
$("chatWide").onclick = () => setChatW($("chat").offsetWidth < innerWidth * 0.45 ? innerWidth * 0.5 : CHAT_W);
// mở / đóng khung chat đổi chỗ trống cho trang
new MutationObserver(() => layout()).observe(document.body, { attributes: true, attributeFilter: ["class"] });

/** Phần tử trang đang hiện (bản gốc, hoặc bản dịch khi chỉ xem bản dịch). */
function viewVisible(p, prefer) {
  const a = pageOf(p, prefer);
  return a?.offsetParent ? a : pageOf(p, prefer === "original" ? "translated" : "original");
}

layout();
Object.assign(window, { viewLayout: layout, viewVisible });
})();
