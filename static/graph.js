// ================================================================ Sơ đồ bài báo (paper-graph, xem graphs.py)
// Tab "🗺 Sơ đồ" trong khung bên phải: trình xem của paper-graph nằm trong <iframe srcdoc> (cùng origin, thừa kế CSP
// của app nên script chèn vào chữ của sơ đồ không chạy — TM-14). Bấm một ô hay "Xem trong bài" thì trình xem gửi
// postMessage {pg:"anchor", page, block}; trang này cuộn hai bản tới đúng đoạn (`p{n}-b{k}` là đoạn k-1 của trang
// n-1, xem graphs.py) và sáng khung đoạn đó.
//
// Bọc trong một hàm như notes.js; dùng biến của app.js và view.js (doc, $, cfg, pageOf, loadLayer, viewScrollTo) và
// chỉ đưa ra ngoài graphReset, graphShown.
(() => {
let ST = null, loadedFor = null, doneAt = -1, timer = null, flashTimer = null;   // doneAt: số trang đã dịch lúc nạp khung
const frame = $("graphFrame"), state = $("graphState"), log = $("graphLog"), btn = $("graphBuild");
const shown = () => document.body.classList.contains("chat") && !$("graphPane").hidden;

function graphReset() {
  clearTimeout(timer);
  ST = null; loadedFor = null;
  frame.hidden = true; frame.removeAttribute("srcdoc");
  log.hidden = true; log.textContent = ""; btn.hidden = true; state.textContent = "";
  if (shown()) setTimeout(refresh, 0);
}
// mở lại tab sau khi dịch thêm trang: nạp lại khung để thẻ có bản dịch của các trang mới
function graphShown() {
  if (!doc) return;
  if (loadedFor === doc.id && doneAt !== doc.done.size) loadedFor = null;
  if (loadedFor !== doc.id) refresh();
}

async function refresh() {
  clearTimeout(timer);
  if (!doc) return;
  const id = doc.id;
  let r;
  try { r = await fetch(`/api/${id}/graph`); } catch { state.textContent = "Không hỏi được server."; return; }
  if (!doc || doc.id !== id) return;                       // đã chuyển sang bài khác trong lúc chờ
  if (!r.ok) { state.textContent = "Không đọc được trạng thái sơ đồ."; return; }
  ST = await r.json();
  draw(id);
}

function draw(id) {
  const job = ST.job, mine = job && !job.other;
  btn.hidden = true;
  log.hidden = !(mine && job.log?.length && (job.running || job.ok === false));   // dựng xong, đạt: thôi hiện log
  if (mine) { log.textContent = job.log.join("\n"); log.scrollTop = log.scrollHeight; }
  if (!ST.available) { state.textContent = "Server chưa cài paper-graph (pip install -e ../paper-graph)."; return; }
  if (job?.running) {
    state.textContent = job.other ? "Đang dựng sơ đồ cho bài khác — chờ xong rồi dựng bài này."
      : `Đang dựng sơ đồ bằng ${job.model}… (${Math.round((Date.now() / 1000 - job.t0) / 60)} phút; thường 5–30 phút)`;
    timer = setTimeout(refresh, mine ? 3000 : 10000);
    return;
  }
  btn.hidden = false;
  btn.textContent = ST.graph ? "Dựng lại" : "Dựng sơ đồ";
  if (ST.graph) {
    state.textContent = (mine && job.ok === false ? "Lần dựng lại chưa đạt, đang xem sơ đồ cũ. " : "") +
      (ST.stale.length ? `Trang ${ST.stale.join(", ")} đã trích lại sau khi dựng: chưa có bản dịch và cuộn tới đoạn ở đó.` : "");
    if (loadedFor !== id) loadFrame(id);
    return;
  }
  frame.hidden = true;
  state.textContent = mine && job.error ? `Dựng lỗi: ${job.error}`
    : ST.draft ? "Lần dựng trước chưa đạt (sơ đồ còn lỗi neo), chưa có gì để xem. Dựng lại?"
    : "Bài này chưa có sơ đồ.";
}

async function loadFrame(id) {
  loadedFor = id; doneAt = doc.done.size;
  const r = await fetch(`/api/${id}/graph/view`);
  if (!doc || doc.id !== id) return;
  if (!r.ok) {
    loadedFor = null;
    state.textContent = (await r.json().catch(() => ({}))).detail || "Không mở được sơ đồ.";
    return;
  }
  frame.srcdoc = await r.text();
  frame.hidden = false;
}

btn.onclick = async () => {
  if (!doc) return;
  const model = ST?.model || "model miễn phí";
  if (!confirm(`Dựng sơ đồ gửi toàn văn bài tới ${model} qua OpenRouter. Nhà cung cấp model miễn phí có thể dùng chữ ` +
               `gửi đi để huấn luyện: chỉ dựng cho bài đã công khai.\n\nMất 5–30 phút, tối đa 32 lượt gọi trong hạn mức ` +
               `miễn phí mỗi ngày.` + (ST?.graph ? " Ô bạn đã khoá giữ nguyên, bản cũ được giữ lại." : "") + "\n\nTiếp tục?")) return;
  btn.disabled = true;
  try {
    const r = await fetch(`/api/${doc.id}/graph/build`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                          body: JSON.stringify({ cfg: cfg(), allow_training: true }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { state.textContent = j.detail || `Lỗi ${r.status}`; return; }
    loadedFor = null;
    ST = j; draw(doc.id);
  } finally { btn.disabled = false; }
};

// ---------------- Bấm ô trong sơ đồ → cuộn hai bản tới đoạn (TM-18: chỉ nhận tin của đúng khung sơ đồ)
addEventListener("message", e => {
  if (e.source !== frame.contentWindow || !doc || !e.data || e.data.pg !== "anchor") return;
  const page = e.data.page;
  if (!Number.isInteger(page) || page < 1 || page > doc.pages) return;
  const m = /^p(\d{1,4})-b(\d{1,4})$/.exec(typeof e.data.block === "string" ? e.data.block : "");
  const idx = m && +m[1] === page && !(ST?.stale || []).includes(page) ? +m[2] - 1 : null;
  // khung sơ đồ che kín bài (điện thoại): bấm "Xem trong bài" thì đóng khung để thấy đoạn; bấm ô để đọc thẻ thì không
  if (e.data.go === true && $("chat").getBoundingClientRect().width >= innerWidth - 40) document.body.classList.remove("chat");
  reveal(page - 1, idx);
});

async function reveal(p, idx) {
  document.querySelectorAll(".blk.goto").forEach(b => b.classList.remove("goto"));
  clearTimeout(flashTimer);
  for (const kind of ["original", "translated"]) {
    const el = pageOf(p, kind);
    if (!el?.offsetParent) continue;                       // bản này đang ẩn (xem một bản)
    if (kind === "translated" && !doc.done.has(p)) continue;
    await loadLayer(el);
    const layer = el.querySelector(".layer"), blk = idx === null ? null : el.querySelector(`.blk[data-b="${idx}"]`);
    const k = +layer?.dataset.w ? el.clientWidth / +layer.dataset.w : 1;
    viewScrollTo(el, blk ? parseFloat(blk.style.top) * k : 0);
    blk?.classList.add("goto");
  }
  flashTimer = setTimeout(() => document.querySelectorAll(".blk.goto").forEach(b => b.classList.remove("goto")), 2500);
}

Object.assign(window, { graphReset, graphShown });
})();
