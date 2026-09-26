// ================================================================ Khung đọc: hai khung xem, thu phóng, kính lúp, bề rộng khung chat
// Bản gốc và bản dịch nằm trong HAI KHUNG XEM độc lập (#paneO | #paneT): mỗi khung tự cuộn (dọc lẫn ngang) và tự thu
// phóng, ở giữa là thanh kéo chia bề rộng. Mặc định hai khung không cuộn theo nhau; nút 🔗 bật lại "cuộn cùng nhau".
// Mỗi bản chỉ có một bản sao của mỗi trang nên lớp chữ, tô vàng, ghi chú vẫn tìm trang bằng pageOf(). Lớp chữ tự co
// theo nhờ ResizeObserver của app.js, vệt tô lưu theo toạ độ PDF nên cũng tự khớp.
// Ảnh trang được vẽ lại nét hơn khi phóng to (server vẽ PNG theo `zoom`): mặc định ×2, tối đa ×4.
//
// Bọc trong một hàm như notes.js; dùng biến của app.js ($, doc, pageOf) và đưa ra ngoài: viewLayout, viewReset,
// viewVisible, viewScrollTo, viewCurrentPage.
(() => {
const Z_MIN = 0.4, Z_MAX = 4, CHAT_W = 380, PAD = 12;
const KINDS = ["original", "translated"], other = k => (k === "original" ? "translated" : "original");
const load = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const pane = { original: $("paneO"), translated: $("paneT") };
const ZKEY = { original: "view_zoom_o", translated: "view_zoom_t" };
// "both" | "original" | "translated"; điện thoại dọc chia đôi thì mỗi bản chỉ còn nửa màn hình → mặc định chỉ bản dịch
let mode = load("view_mode", matchMedia("(max-width:700px)").matches ? "translated" : "both");
const zoom = {};
for (const k of KINDS) zoom[k] = clamp(parseFloat(load(ZKEY[k], load("view_zoom", "1"))) || 1, Z_MIN, Z_MAX);
let split = clamp(parseFloat(load("view_split", "0.5")) || 0.5, 0.2, 0.8);
let link = load("view_link", "0") === "1";
let active = mode === "translated" ? "translated" : "original";   // khung đang chỉnh: chuột vừa vào / đang hiện
const mark = {};                             // chỗ đang đọc của từng khung, để hiện lại đúng chỗ sau khi bị ẩn

const shown = k => mode === "both" || mode === k;

// ---------------- Bố cục: bề rộng trang = bề rộng khung × zoom của khung
function layout() {
  const cols = $("cols");
  cols.classList.toggle("only-original", mode === "original");
  cols.classList.toggle("only-translated", mode === "translated");
  cols.style.setProperty("--split", split);
  const top = document.querySelector("header").offsetHeight + (document.querySelector(".bar")?.offsetHeight || 0);
  cols.style.setProperty("--paneh", Math.max(200, innerHeight - top) + "px");
  for (const k of KINDS) {
    if (!shown(k)) continue;
    pane[k].style.setProperty("--pgw", Math.max(160, Math.floor((pane[k].clientWidth - 2 * PAD) * zoom[k])) + "px");
  }
  if (!shown(active)) active = other(active);
  $("zoomPct").textContent = Math.round(zoom[active] * 100) + "%";
  $("zoomWho").textContent = mode === "both" ? (active === "original" ? "Gốc" : "Dịch") : "";
  $("linkScroll").classList.toggle("on", link);
  $("linkScroll").hidden = mode !== "both";
  document.querySelectorAll("#viewbar [data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  sharpenSoon();
}

/** Trang của khung k nằm tại điểm pt (toạ độ màn hình), và pt nằm ở đâu trên trang đó (0–1). */
function anchorAt(k, pt) {
  const pr = pane[k].getBoundingClientRect();
  pt = pt || { x: pr.left + pr.width / 2, y: pr.top + pr.height / 2 };
  let best = null, dist = Infinity;
  for (const el of pane[k].querySelectorAll(".page")) {
    const r = el.getBoundingClientRect();
    const d = pt.y < r.top ? r.top - pt.y : pt.y > r.bottom ? pt.y - r.bottom : 0;
    if (d < dist) { dist = d; best = { p: el.dataset.p, fx: (pt.x - r.left) / r.width, fy: (pt.y - r.top) / r.height }; }
    if (!d || r.top > pt.y) break;
  }
  return best && { ...best, x: pt.x - pr.left, y: pt.y - pr.top };
}
/** Cuộn khung k để điểm đã neo quay về đúng chỗ cũ trên khung. */
function restore(k, a) {
  const el = a && pane[k].querySelector(`.page[data-p="${a.p}"]`);
  if (!el) return;
  const r = el.getBoundingClientRect(), pr = pane[k].getBoundingClientRect();
  const dx = r.left + a.fx * r.width - (pr.left + a.x), dy = r.top + a.fy * r.height - (pr.top + a.y);
  // gán scrollTop (dù không đổi) là huỷ lượt cuộn mượt đang chạy, vd. vừa bấm tới ghi chú → chỉ gán khi thật sự lệch
  if (Math.abs(dx) >= 1) pane[k].scrollLeft += dx;
  if (Math.abs(dy) >= 1) pane[k].scrollTop += dy;
}
/** Đổi bố cục mà mỗi khung vẫn giữ nguyên chỗ đang đọc (thu phóng quanh con trỏ / giữa khung, như trình xem PDF).
 *  Khung sắp bị ẩn thì nhớ chỗ lại để lúc hiện ra cuộn về đó. */
function relayout(change, pt, who) {
  for (const k of KINDS) if (shown(k)) mark[k] = anchorAt(k, k === who ? pt : null);
  change?.();
  layout();
  for (const k of KINDS) if (shown(k)) restore(k, mark[k]);
}
function setZoom(k, z, pt) {
  z = clamp(Math.round(z * 100) / 100, Z_MIN, Z_MAX);
  if (z === zoom[k]) return;
  relayout(() => { zoom[k] = z; save(ZKEY[k], z); }, pt, k);
}
function setMode(m) { relayout(() => { mode = m; save("view_mode", m); if (m !== "both") active = m; }); }
function setActive(k) {
  if (active === k || !shown(k)) return;
  active = k;
  layout();
}

$("viewbar").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.mode) setMode(b.dataset.mode);
  else if (b.id === "zoomIn") setZoom(active, zoom[active] * 1.2);
  else if (b.id === "zoomOut") setZoom(active, zoom[active] / 1.2);
  else if (b.id === "zoomFit" || b.id === "zoomPct") setZoom(active, 1);
  else if (b.id === "linkScroll") { link = !link; save("view_link", link ? "1" : "0"); layout(); follow(active); }
});
for (const k of KINDS) {
  pane[k].addEventListener("pointerenter", () => setActive(k));
  pane[k].addEventListener("pointerdown", () => setActive(k));
  pane[k].addEventListener("scroll", () => follow(k), { passive: true });
}
window.addEventListener("wheel", e => {
  // ⌥ + lăn chuột khi đang soi kính lúp: đổi độ phóng của kính
  if (e.altKey && !lens.hidden) { e.preventDefault(); setLensK(lensK * Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.003))); return; }
  // ⌘/Ctrl + lăn chuột (và chụm hai ngón trên trackpad — trình duyệt báo thành wheel có ctrlKey): thu phóng khung dưới chuột
  const p = (e.ctrlKey || e.metaKey) && e.target.closest?.(".vpane");
  if (!p) return;
  e.preventDefault();
  setActive(p.dataset.kind);
  setZoom(p.dataset.kind, zoom[p.dataset.kind] * Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.004)), { x: e.clientX, y: e.clientY });
}, { passive: false });
// Điện thoại: bàn phím bật/tắt, thanh địa chỉ co giãn chỉ đổi chiều cao → chỉ đặt lại chiều cao khung, không neo lại
// chỗ đọc (neo theo giữa khung mà giữa khung dời đi thì trang bị kéo giật theo)
let lastW = innerWidth;
window.addEventListener("resize", () => {
  if (innerWidth !== lastW) { lastW = innerWidth; relayout(); return; }
  const top = document.querySelector("header").offsetHeight + (document.querySelector(".bar")?.offsetHeight || 0);
  $("cols").style.setProperty("--paneh", Math.max(200, innerHeight - top) + "px");
});

// ---------------- Chụm hai ngón (màn cảm ứng): thu phóng khung dưới tay, quanh điểm giữa hai ngón
// Trong lúc chụm chỉ phóng tạm cả cột trang bằng transform (GPU, không dựng lại gì); thả tay mới đặt zoom thật —
// đổi bề rộng trang ở mỗi nhịp touchmove thì mọi lớp chữ co giãn lại theo, giật thấy rõ.
let pinch = null;
const tdist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
for (const k of KINDS) {
  const stack = pane[k].querySelector(".stack");
  pane[k].addEventListener("touchstart", e => {
    if (e.touches.length !== 2) return;
    const [a, b] = e.touches, sr = stack.getBoundingClientRect();
    const c = { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 };
    pinch = { k, stack, c, d0: tdist(a, b) || 1, s: 1 };
    setActive(k);
    stack.style.transformOrigin = `${c.x - sr.left}px ${c.y - sr.top}px`;
    stack.style.willChange = "transform";
  }, { passive: true });
  pane[k].addEventListener("touchmove", e => {
    if (!pinch || pinch.k !== k || e.touches.length !== 2) return;
    e.preventDefault();
    const z = clamp(zoom[k] * tdist(e.touches[0], e.touches[1]) / pinch.d0, Z_MIN, Z_MAX);
    pinch.s = z / zoom[k];
    stack.style.transform = `scale(${pinch.s})`;
  }, { passive: false });
  const end = e => {
    if (!pinch || pinch.k !== k || e.touches.length >= 2) return;
    const { s, c } = pinch;
    pinch = null;
    stack.style.transform = stack.style.transformOrigin = stack.style.willChange = "";
    if (Math.abs(s - 1) > 0.02) setZoom(k, zoom[k] * s, c);
  };
  pane[k].addEventListener("touchend", end);
  pane[k].addEventListener("touchcancel", end);
}
// Safari iOS vẫn phóng cả trang web bằng cử chỉ riêng của nó dù đã có touch-action
document.addEventListener("gesturestart", e => { if (e.target.closest?.(".vpane")) e.preventDefault(); });
new ResizeObserver(() => relayout()).observe(document.querySelector("header"));   // thanh trên cùng xuống dòng

// ---------------- Cuộn cùng nhau (🔗, mặc định tắt): chỉ khung đang dùng kéo khung kia theo — không vòng lặp
function follow(k) {
  sharpenSoon();
  if (!link || mode !== "both" || k !== active) return;
  const o = other(k), pr = pane[k].getBoundingClientRect(), a = anchorAt(k, { x: pr.left + pr.width / 2, y: pr.top + 1 });
  const el = a && pane[o].querySelector(`.page[data-p="${a.p}"]`);
  if (!el) return;
  const r = el.getBoundingClientRect(), qr = pane[o].getBoundingClientRect();
  pane[o].scrollTop += r.top + a.fy * r.height - (qr.top + 1);
}

// ---------------- Thanh chia bề rộng hai khung
$("split").addEventListener("pointerdown", e => {
  e.preventDefault();
  const grip = e.currentTarget, cr = $("cols").getBoundingClientRect();
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("splitting");
  const move = ev => relayout(() => { split = clamp((ev.clientX - cr.left) / (cr.width - 6), 0.2, 0.8); });
  const up = () => {
    grip.removeEventListener("pointermove", move);
    document.body.classList.remove("splitting");
    save("view_split", split);
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up, { once: true });
  grip.addEventListener("pointercancel", up, { once: true });
});
$("split").addEventListener("dblclick", () => { relayout(() => { split = 0.5; }); save("view_split", split); });

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
      if (r.width && r.bottom > -innerHeight && r.top < 2 * innerHeight) sharpen(img);
    }
  }, 300);
}
$("cols").addEventListener("load", e => { if (e.target.tagName === "IMG") sharpen(e.target); }, true);

// ---------------- Kính lúp: giữ ⌥ Option / Alt trong khi rê chuột trên trang
// Khung chữ nhật ngang (đọc trọn một dòng), phóng ×1,8 mặc định; ⌥ + lăn chuột đổi trong khoảng ×1,4–×3 (nhớ lại).
// Phóng từ ảnh đang hiện thì mờ (ở 100% ảnh chỉ vẽ ×2), nên kính lúp tải riêng bản ×4 của trang đó; trong lúc
// chờ thì soi tạm trên ảnh đang hiện. Bản ×4 đã tải được nhớ lại (trình duyệt cũng giữ trong bộ nhớ đệm).
const lens = $("lens"), HI = new Map();       // url ảnh đang hiện → url bản ×4 đã tải xong
let lensK = clamp(parseFloat(load("lens_k", "1.8")) || 1.8, 1.4, 3), mouse = null;
function setLensK(k) {
  lensK = clamp(Math.round(k * 10) / 10, 1.4, 3);
  save("lens_k", lensK);
  if (mouse) lensAt(mouse);
}
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
  lens.hidden = false;
  const r = img.getBoundingClientRect(), RX = lens.offsetWidth / 2, RY = lens.offsetHeight / 2;
  lens.dataset.k = "×" + lensK.toFixed(1).replace(".", ",");
  lens.style.left = ev.clientX - RX + "px";
  lens.style.top = ev.clientY - RY + "px";
  lens.style.backgroundImage = `url("${hiRes(img)}")`;
  lens.style.backgroundSize = `${r.width * lensK}px ${r.height * lensK}px`;
  lens.style.backgroundPosition = `${RX - (ev.clientX - r.left) * lensK}px ${RY - (ev.clientY - r.top) * lensK}px`;
}
document.addEventListener("mousemove", e => {
  mouse = e;
  if (e.altKey) lensAt(e); else if (!lens.hidden) lens.hidden = true;
}, { passive: true });
document.addEventListener("keydown", e => { if (e.key === "Alt" && mouse) lensAt(mouse); });
document.addEventListener("keyup", e => { if (e.key === "Alt") lens.hidden = true; });
window.addEventListener("blur", () => { lens.hidden = true; });
document.addEventListener("scroll", () => { if (!lens.hidden && mouse) lensAt(mouse); }, { capture: true, passive: true });

// ---------------- Kéo rộng / thu hẹp khung chat
function setChatW(w, keep = true) {
  w = Math.round(Math.min(innerWidth * 0.7, Math.max(300, w)));
  relayout(() => document.documentElement.style.setProperty("--chatw", w + "px"));
  if (keep) save("chat_w", w);
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
// mở / đóng khung chat đổi chỗ trống cho trang (lớp của body đã đổi, nhưng trang chưa đổi cỡ nên neo vẫn đúng)
new MutationObserver(() => relayout()).observe(document.body, { attributes: true, attributeFilter: ["class"] });

/** Phần tử trang đang hiện (bản gốc, hoặc bản dịch khi chỉ xem bản dịch). */
function viewVisible(p, prefer) {
  const a = pageOf(p, prefer);
  return a?.offsetParent ? a : pageOf(p, prefer === "original" ? "translated" : "original");
}
/** Cuộn khung chứa trang `el` tới điểm cách đầu trang dy (đơn vị hiển thị), chừa một khoảng phía trên. */
function viewScrollTo(el, dy = 0) {
  const p = el.closest(".vpane");
  if (!p) return;
  setActive(p.dataset.kind);
  const r = el.getBoundingClientRect(), pr = p.getBoundingClientRect();
  const left = r.left < pr.left || r.right > pr.right ? p.scrollLeft + r.left - pr.left - PAD : p.scrollLeft;
  // bảng Cấu hình/Thuật ngữ đang mở đẩy khung xuống dưới màn hình → cuộn cửa sổ cho thấy trọn khung (không "smooth":
  // hai lượt cuộn mượt cùng lúc thì trình duyệt huỷ lượt cuộn trong khung)
  const below = $("cols").getBoundingClientRect().bottom - innerHeight;
  if (below > 1) window.scrollBy(0, below);
  p.scrollTo({ top: p.scrollTop + r.top - pr.top + dy - 80, left, behavior: "smooth" });
}
/** Trang ở đầu khung đang dùng (trang có phần nằm dưới mép trên khung ≥ 80px, gần mép trên nhất). */
function viewCurrentPage() {
  const k = shown(active) ? active : other(active), top = pane[k].getBoundingClientRect().top;
  let best = null, d = Infinity;
  for (const el of pane[k].querySelectorAll(".page")) {
    const r = el.getBoundingClientRect();
    if (r.bottom > top + 80 && Math.abs(r.top - top) < d) { d = Math.abs(r.top - top); best = +el.dataset.p; }
    if (r.top > top + innerHeight) break;
  }
  return best;
}
/** Tài liệu mới: hai khung về đầu trang, quên chỗ đọc cũ. */
function viewReset() {
  for (const k of KINDS) { pane[k].scrollTop = pane[k].scrollLeft = 0; delete mark[k]; }
  layout();
}

layout();
Object.assign(window, { viewLayout: layout, viewReset, viewVisible, viewScrollTo, viewCurrentPage });
})();
