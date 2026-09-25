// ================================================================ Ghi chú
// Ghi chú chung cho cả bài (ô tự lưu), ghi chú gắn vào vệt tô vàng (nút 📝 trên thanh nổi khi bôi đen), ghi chú
// lưu từ câu trả lời LLM (📌 dưới câu trả lời), và "💬 Hỏi lại" để gửi một ghi chú kèm câu hỏi mới.
// Lưu ở server cùng tài liệu (notes.py, state.json); chữ ghi chú là Markdown, dựng bằng render.js.
//
// Bọc trong một hàm như render.js: file nạp chung phạm vi toàn cục với app.js, trùng một tên `const` là
// SyntaxError. Dùng biến của app.js (doc, MARKS, $, esc, pageOf, marksHit, addMarks, fmtTime…) và chỉ đưa ra
// ngoài các hàm móc nối mà app.js gọi: notesReset, notesLoad, paintNoteDots, noteSaveButton, noteChatExtra,
// noteAfterSend, notesMarksChanged, showTab, noteAdded.
(() => {
const GEN = "general";
let NOTES = [], attached = [], draft = null, genTimer = null, genPending = null;
const byId = id => NOTES.find(n => n.id === id);
const genKey = id => `note_general:${id}`;
const post = (id, body) => fetch(`/api/${id}/notes`, { method:"POST", headers:{"Content-Type":"application/json"},
                                                      body: JSON.stringify(body) });
const upsertLocal = n => { const i = NOTES.findIndex(x => x.id === n.id); if (i >= 0) NOTES[i] = n; else NOTES.push(n); };
const hasText = n => !!(n && n.text && n.text.trim());
const markOf = n => n.mark_id && (MARKS[n.page] || []).find(m => m.id === n.mark_id);
const where = n => n.id === GEN ? "Ghi chú chung" : n.page == null ? "Cả bài" : `Trang ${n.page + 1}`;

// ---------------- Tab trò chuyện / ghi chú
function showTab(which) {
  document.body.classList.add("chat");
  $("tabChat").classList.toggle("on", which === "chat");
  $("tabNotes").classList.toggle("on", which === "notes");
  $("chatPane").hidden = which !== "chat";
  $("notesPane").hidden = which !== "notes";
}
const tabOpen = which => document.body.classList.contains("chat") && !$(which === "chat" ? "chatPane" : "notesPane").hidden;
$("tabChat").onclick = () => showTab("chat");
$("tabNotes").onclick = () => showTab("notes");
$("btnChat").onclick = () => tabOpen("chat") ? document.body.classList.remove("chat") : showTab("chat");
$("btnNotes").onclick = () => tabOpen("notes") ? document.body.classList.remove("chat") : showTab("notes");

// ---------------- Nạp / vẽ
function notesLoad(list) {
  NOTES = list || [];
  // Bản sao trong trình duyệt ({text, t}) chỉ thắng khi gõ SAU lần lưu cuối trên server (lần trước chưa kịp lưu):
  // bản cũ hơn (đã sửa ở trình duyệt khác rồi) thì bỏ, đừng ghi đè lên chữ mới hơn
  const gen = byId(GEN), text = gen ? gen.text : "";
  let local = null;
  try { local = JSON.parse(localStorage.getItem(genKey(doc.id)) || "null"); } catch {}
  const newer = local && typeof local.text === "string" && local.t > (gen ? gen.updated * 1000 : 0) && local.text !== text;
  $("genNote").value = newer ? local.text : text;
  $("genState").textContent = "";
  if (newer) queueGeneral();                              // lần trước gõ xong chưa kịp lưu lên server → lưu nốt
  else if (local) try { localStorage.removeItem(genKey(doc.id)); } catch {}
  drawNotes();
  new Set([...NOTES.map(n => n.page), ...Object.keys(MARKS).map(Number)]).forEach(p => paintNoteDots(p));
}
function notesReset() {
  flushGeneral();
  NOTES = []; attached = []; hideDraft();
  $("genNote").value = ""; $("genState").textContent = "";
  showAttached();
  drawNotes();
}
function drawCount() {
  const k = NOTES.filter(hasText).length;
  $("noteCount").textContent = k ? `(${k})` : "";
  $("nsum").textContent = k ? `${k} ghi chú` : "";
  $("notesMd").href = doc ? `/api/${doc.id}/notes.md` : "#";
}
function drawNotes() {
  const box = $("noteList"), list = NOTES.filter(n => n.id !== GEN);
  box.innerHTML = list.length ? "" : `<div class="hint">Chưa có ghi chú nào. Bôi đen một đoạn rồi bấm “📝 Ghi chú”,
    hoặc bấm “📌 Lưu thành ghi chú” dưới câu trả lời của LLM.</div>`;
  for (const n of list) box.appendChild(card(n));
  drawCount();
}
function card(n) {
  const el = document.createElement("div"), m = markOf(n);
  el.className = "note";
  el.dataset.id = n.id;
  const kind = n.source === "llm" ? `🤖 LLM${n.meta?.model ? " · " + esc(n.meta.model) : ""}`
             : n.mark_id ? (m ? "🖍 vệt tô" : "🖍 vệt tô đã bỏ") : "";
  el.innerHTML = `<div class="nmeta">${n.page != null ? `<a class="ngo" title="Tới chỗ này trong bài">${where(n)}</a>`
      : `<span>${where(n)}</span>`}${kind ? `<span>· ${kind}</span>` : ""}<span>· ${fmtTime(n.updated)}</span></div>
    ${n.meta?.question ? `<div class="nquote">❓ ${esc(n.meta.question)}</div>` : ""}
    ${n.quote ? `<div class="nquote">“${esc(n.quote)}”</div>` : ""}
    <div class="nbody"></div>
    <div class="msgbar"><button class="nedit" type="button">✏️ Sửa</button>
      <button class="nask" type="button" title="Hỏi LLM tiếp, gửi kèm ghi chú này">💬 Hỏi lại</button>
      <button class="ncopy" type="button" title="Chép dạng Markdown">📋 Chép</button>
      <button class="ndel" type="button" title="Xoá ghi chú">🗑</button></div>`;
  el.querySelector(".nbody").innerHTML = renderMarkdown(n.text);
  return el;
}
function focusNote(n) {
  if (!n) return;
  showTab("notes");
  const el = $("noteList").querySelector(`.note[data-id="${n.id}"]`);
  if (!el) return;
  el.scrollIntoView({ block:"nearest", behavior:"smooth" });
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1200);
}
/** Cuộn tới chỗ của ghi chú trong bài (vệt tô của nó, hoặc đầu trang). */
function goTo(n) {
  const m = markOf(n), el = viewVisible(n.page, m ? m.side : "original");   // bản đó đang ẩn (xem một cột) → bản kia
  if (!el) return;
  const layer = el.querySelector(".layer"), k = +layer?.dataset.w ? el.clientWidth / +layer.dataset.w : 1;
  const r = el.dataset.kind === m?.side ? m.rects?.[0] : null;
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY + (r ? r[1] * k : 0) - 140, behavior:"smooth" });
}

// ---------------- Dấu 📝 trên vệt tô có ghi chú, và số ghi chú của trang ở góc bản gốc
function paintNoteDots(p) {
  if (p == null || !doc) return;
  const withNote = new Set(NOTES.filter(n => n.page === p && n.mark_id && hasText(n)).map(n => n.mark_id));
  for (const kind of ["original", "translated"]) {
    const layer = pageOf(p, kind)?.querySelector(".layer");
    if (!layer) continue;
    layer.querySelectorAll(".ndot").forEach(x => x.remove());
    for (const m of MARKS[p] || []) {
      const rs = (m.side === kind ? m.rects : syncOn() ? m.peer : []) || [];
      if (!withNote.has(m.id) || !rs.length) continue;
      const r = rs[rs.length - 1], d = document.createElement("span");
      d.className = "ndot";
      d.textContent = "📝";
      d.title = "Có ghi chú — bấm để xem";
      d.style.cssText = `left:${r[2] + 1}px;top:${r[1] - 4}px`;
      d.onmousedown = e => e.preventDefault();          // không bắt đầu bôi đen khi bấm vào dấu
      d.onclick = e => { e.stopPropagation(); focusNote(NOTES.find(n => n.mark_id === m.id && hasText(n))); };
      layer.appendChild(d);
    }
  }
  const el = pageOf(p, "original");
  if (!el) return;
  el.querySelector(".pnote")?.remove();
  const mine = NOTES.filter(n => n.page === p && hasText(n));
  if (!mine.length) return;
  const b = document.createElement("span");
  b.className = "pnote";
  b.textContent = `📝 ${mine.length}`;
  b.title = `${mine.length} ghi chú ở trang này — bấm để xem`;
  b.onclick = () => focusNote(mine[0]);
  el.appendChild(b);
}

// ---------------- Ghi chú chung: tự lưu sau 1 giây ngừng gõ; bản nháp giữ trong trình duyệt tới khi server nhận
function queueGeneral() {
  if (!doc) return;
  genPending = { id: doc.id, text: $("genNote").value };
  try { localStorage.setItem(genKey(doc.id), JSON.stringify({ text: genPending.text, t: Date.now() })); } catch {}
  $("genState").textContent = "đang gõ…";
  clearTimeout(genTimer);
  genTimer = setTimeout(flushGeneral, 1000);
}
async function flushGeneral() {
  clearTimeout(genTimer);
  genTimer = null;
  const job = genPending;
  if (!job) return;
  genPending = null;
  try {
    const r = await post(job.id, { id: GEN, text: job.text });
    if (!r.ok) throw new Error(r.statusText);
    const n = await r.json();
    if (genPending?.id !== job.id) { try { localStorage.removeItem(genKey(job.id)); } catch {} }
    if (doc?.id !== job.id) return;                       // đã chuyển sang bài khác trong lúc chờ
    upsertLocal(n);
    if (!genPending) $("genState").textContent = "✓ đã lưu";
    drawCount();
  } catch {
    if (doc?.id === job.id) $("genState").textContent = "⚠️ chưa lưu được lên server (vẫn giữ trong trình duyệt)";
  }
}
$("genNote").addEventListener("input", queueGeneral);
$("genNote").addEventListener("blur", () => genPending && flushGeneral());
window.addEventListener("beforeunload", () => {     // tắt tab giữa lúc chờ 1 giây: gửi nốt bằng keepalive
  if (!genPending) return;
  fetch(`/api/${genPending.id}/notes`, { method:"POST", keepalive:true, headers:{"Content-Type":"application/json"},
                                        body: JSON.stringify({ id: GEN, text: genPending.text }) });
});

// ---------------- Ghi chú mới cho đoạn đang bôi đen (thanh nổi → 📝 Ghi chú)
$("selNote").onclick = async () => {
  const info = sel;
  $("selbar").classList.remove("on");
  if (!info || !doc) return;
  let m = marksHit(info)[0];                  // bôi đè lên vệt có sẵn → ghi chú cho vệt đó
  if (!m && info.blocks.length) m = (await addMarks(info))?.[0];   // chưa tô → tô vàng luôn
  window.getSelection().removeAllRanges();
  openDraft({ page: info.page, mark_id: m?.id || null, quote: (m?.text || info.text).slice(0, 2000) });
};
function openDraft(d) {
  draft = d;
  showTab("notes");
  $("noteDraft").hidden = false;
  $("draftWhere").textContent = `Ghi chú mới · Trang ${d.page + 1}${d.mark_id ? " · 🖍 vệt tô" : ""}: “${d.quote.slice(0, 160)}”`;
  $("draftText").value = "";
  $("draftText").focus();
  $("noteDraft").scrollIntoView({ block:"nearest" });
}
function hideDraft() { draft = null; $("noteDraft").hidden = true; }
async function saveDraft() {
  const text = $("draftText").value.trim(), d = draft, mine = doc;
  if (!d || !mine) return;
  if (!text) { $("draftText").focus(); return; }
  const r = await post(mine.id, { ...d, text });
  if (!r.ok) { setStatus("Không lưu được ghi chú: " + r.statusText); return; }
  if (doc !== mine) return;
  upsertLocal(await r.json());
  hideDraft();
  drawNotes();
  paintNoteDots(d.page);
}
$("draftSave").onclick = saveDraft;
$("draftCancel").onclick = hideDraft;
$("draftText").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveDraft(); }
  if (e.key === "Escape") hideDraft();
});

// ---------------- Sửa / hỏi lại / chép / xoá
$("noteList").onclick = async e => {
  const el = e.target.closest(".note"), n = el && byId(el.dataset.id);
  if (!n) return;
  if (e.target.closest(".ngo")) goTo(n);
  else if (e.target.closest(".nedit")) editCard(el, n);
  else if (e.target.closest(".nask")) attach(n);
  else if (e.target.closest(".ncopy")) {
    const b = e.target.closest(".ncopy");
    try { await navigator.clipboard.writeText(n.text); b.textContent = "✓ Đã chép"; } catch { b.textContent = "✗"; }
    setTimeout(() => { b.textContent = "📋 Chép"; }, 1500);
  } else if (e.target.closest(".ndel")) {
    if (!confirm("Xoá ghi chú này?")) return;
    const r = await fetch(`/api/${doc.id}/notes/${n.id}`, { method:"DELETE" });
    if (!r.ok && r.status !== 404) { setStatus("Không xoá được ghi chú"); return; }
    NOTES = NOTES.filter(x => x.id !== n.id);
    attached = attached.filter(x => x !== n.id);
    showAttached();
    drawNotes();
    paintNoteDots(n.page);
  }
};
function editCard(el, n) {
  const body = el.querySelector(".nbody"), bar = el.querySelector(".msgbar");
  if (body.querySelector("textarea")) return;
  body.innerHTML = `<textarea rows="${Math.min(14, Math.max(4, n.text.split("\n").length + 1))}"></textarea>`;
  const ta = body.querySelector("textarea");
  ta.value = n.text;
  bar.hidden = true;
  const edit = document.createElement("div");
  edit.className = "msgbar";
  edit.innerHTML = `<button class="primary" type="button">Lưu</button><button type="button">Huỷ</button>`;
  body.after(edit);
  const done = () => { drawNotes(); paintNoteDots(n.page); };
  const save = async () => {
    const text = ta.value.trim(), mine = doc;
    if (!text) {                                          // xoá hết chữ = xoá ghi chú
      if (!confirm("Ghi chú trống — xoá ghi chú này?")) return;
      await fetch(`/api/${mine.id}/notes/${n.id}`, { method:"DELETE" });
      NOTES = NOTES.filter(x => x.id !== n.id);
      return done();
    }
    const r = await post(mine.id, { id: n.id, text });
    if (!r.ok) { setStatus("Không lưu được ghi chú: " + r.statusText); return; }
    if (doc === mine) { upsertLocal(await r.json()); done(); }
  };
  edit.children[0].onclick = save;
  edit.children[1].onclick = done;
  ta.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    if (e.key === "Escape") done();
  });
  ta.focus();
}

// ---------------- 💬 Hỏi lại: gửi ghi chú kèm câu hỏi kế tiếp
function attach(n) {
  attached = [n.id];
  showAttached();
  showTab("chat");
  $("q").focus();
}
function showAttached() {
  const n = attached.length ? byId(attached[0]) : null;
  $("notebox").hidden = !n;
  if (n) $("notetext").textContent = `📝 Kèm ghi chú (${where(n)}): ${n.text.slice(0, 200)}`;
}
$("noteDrop").onclick = () => { attached = []; showAttached(); };
/** Phần thêm cho request trò chuyện: ghi chú đang kèm, trang của nó, và chữ hiện ở bong bóng câu hỏi. */
function noteChatExtra() {
  const n = attached.length ? byId(attached[0]) : null;
  return { note_ids: n ? [n.id] : [], page: n ? n.page : null, quote: n ? "📝 " + n.text.slice(0, 280) : "" };
}
const noteAfterSend = () => { attached = []; showAttached(); };

// ---------------- 📌 Lưu câu trả lời LLM thành ghi chú
/** Vệt tô ứng với đoạn bôi đen lúc hỏi (nếu đoạn đó đã được tô), để ghi chú gắn đúng vệt. */
function guessMark(used) {
  if (!used) return null;
  const t = used.text.trim();
  const m = (MARKS[used.page] || []).find(m => m.side === used.side && used.blocks.includes(m.block) &&
                                              (t.includes(m.text) || m.text.includes(t)));
  return m ? m.id : null;
}
function noteSaveButton(el, bar) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = "📌 Lưu thành ghi chú";
  b.title = "Lưu câu trả lời này vào ghi chú của bài (kèm câu hỏi và đoạn đã bôi đen)";
  b.onclick = async () => {
    const used = el._used, mine = doc, c = cfg();
    if (!mine) return;
    b.disabled = true;
    const r = await post(mine.id, {
      text: el._src, source: "llm", page: used ? used.page : (el._notePage ?? null), mark_id: guessMark(used),
      quote: used ? used.text : "", meta: { question: el._q || "", model: c.chat_model || c.model } });
    if (!r.ok) { b.disabled = false; b.textContent = "✗ Không lưu được"; return; }
    if (doc === mine) { const n = await r.json(); upsertLocal(n); drawNotes(); paintNoteDots(n.page); }
    b.textContent = "✓ Đã lưu vào ghi chú";
  };
  bar.appendChild(b);
}
/** Vệt tô vừa thêm/bỏ: nhãn "vệt tô đã bỏ" và dấu 📝 phải theo kịp. */
function notesMarksChanged(p) {
  if (NOTES.some(n => n.page === p && n.mark_id)) drawNotes();
}

/** Ghi chú vừa được server tạo ở chỗ khác (📝 Tổng hợp phiên trò chuyện): thêm vào danh sách và mở ra xem. */
function noteAdded(n) {
  upsertLocal(n);
  drawNotes();
  paintNoteDots(n.page);
  focusNote(n);
}

Object.assign(window, { notesReset, notesLoad, paintNoteDots, noteSaveButton, noteChatExtra, noteAfterSend,
                        notesMarksChanged, showTab, noteAdded });
})();
