const PADDLE_OCR = "hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF:latest";  // OCR tốt nhất đã đo (18/09/2026)
// Nhà cung cấp: nạp từ server (/api/providers — có sẵn + của người dùng, xem app.py). Mỗi mục: base_url, model (dịch,
// trò chuyện), vision (OCR), gloss (lập bảng thuật ngữ; không có thì như model), parallel (số vùng OCR cùng lúc).
let PRESETS = { custom: {} };
const $ = id => document.getElementById(id);
// Ô chữ thường (lưu nguyên giá trị) và ô đánh dấu (lưu .checked). API key lưu riêng theo server, xem KEY_OF.
const FIELDS = ["preset","ocr_preset","chat_preset","base_url","model","temperature","concurrency","glossary",
  "translator","length_ratio","page_span","ocr_mode","ocr_engine","ocr_lang","ocr_vlm_base_url","ocr_vlm_model",
  "ocr_vlm_concurrency","ocr_custom_url","chat_base_url","chat_model","gloss_preset","gloss_base_url","gloss_model",
  "align_preset","align_base_url","align_model",
  "font_serif","font_sans","font_mono"];
const CHECKS = ["glossary_auto","google_fallback","disable_thinking","chat_same","gloss_same","align_llm","align_same","sync_marks"];
// Ô API key → ô Base URL của server dùng key đó ("ocrspace" là dịch vụ cố định, không có base URL)
const KEY_OF = { api_key:"base_url", ocr_vlm_api_key:"ocr_vlm_base_url", chat_api_key:"chat_base_url", gloss_api_key:"gloss_base_url",
                 align_api_key:"align_base_url",
                 ocr_custom_api_key:"ocr_custom_url", ocr_api_key:"ocrspace" };
let doc = null, stopFlag = false;

// ================================================================ Cấu hình & API key
// Key lưu theo TỪNG server: đổi preset OpenRouter → Ollama → OpenRouter không phải gõ lại key.
// Server cũng giữ một bản (data/keys.json) nên xoá dữ liệu duyệt web hay mở app từ máy khác vẫn dịch được;
// bản trên server chỉ được trả về dạng che (sk-o…9f2c) để hiện vào placeholder.
let SERVER_KEYS = {};
const slotOf = f => (KEY_OF[f] === "ocrspace" ? "ocrspace" : ($(KEY_OF[f])?.value || "")).replace(/\/+$/, "");
const keyStore = () => { try { return JSON.parse(localStorage.getItem("apikeys") || "{}"); } catch { return {}; } };

function rememberKey(f) {
  const slot = slotOf(f), ks = keyStore();
  if (!slot) return;
  if ($(f).value) ks[slot] = $(f).value; else delete ks[slot];
  try { localStorage.setItem("apikeys", JSON.stringify(ks)); } catch {}
}
function fillKey(f) {
  const slot = slotOf(f);
  $(f).value = keyStore()[slot] || "";
  $(f).placeholder = SERVER_KEYS[slot] ? `đã lưu trên server (${SERVER_KEYS[slot]})`
                   : (f === "ocr_api_key" ? "helloworld (demo)" : "(để trống nếu local)");
}
const fillKeys = () => Object.keys(KEY_OF).forEach(fillKey);

function loadSettings() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("llm") || "{}"); } catch {}
  const P = id => PRESETS[id] || {};
  s = { preset:"ollama", base_url:P("ollama").base_url, model:P("ollama").model, temperature:0.2, concurrency:1, glossary:"", glossary_auto:true, ...s };
  s = { ocr_preset:"ollama", chat_preset:"ollama", translator:"llm", length_ratio:1.3, page_span:0, ocr_mode:"auto",
        ocr_engine:"rapidocr", ocr_lang:"eng", ocr_vlm_base_url:"http://localhost:11434/v1", ocr_vlm_model:PADDLE_OCR,
        ocr_vlm_concurrency:4, ocr_custom_url:"", google_fallback:true, chat_same:true, chat_base_url:"",
        chat_model:"", gloss_preset:"openrouter", gloss_same:true, gloss_base_url:P("openrouter").base_url,
        gloss_model:P("openrouter").gloss, align_llm:true, align_same:false, align_preset:"chatgpt",
        align_base_url:P("chatgpt").base_url, align_model:P("chatgpt").model, sync_marks:false, font_serif:"auto", font_sans:"auto", font_mono:"auto", ...s };
  const ver = s.settings_v || 1;
  // v2: model OCR mặc định đổi từ qwen3.5 sang PaddleOCR-VL — cập nhật cài đặt cũ còn để mặc định cũ
  if (ver < 2 && s.ocr_vlm_model === "qwen3.5:latest") s.ocr_vlm_model = PADDLE_OCR;
  FIELDS.forEach(f => $(f).value = s[f] ?? "");
  // v3: key chuyển sang lưu theo từng server; ô đánh dấu trước đây lưu nhầm vào .value nên glossary_auto
  // không bao giờ được tích lại → đọc lại theo kiểu boolean
  if (ver < 3) {
    const ks = keyStore();
    for (const [f, urlField] of Object.entries(KEY_OF)) {
      const slot = (urlField === "ocrspace" ? "ocrspace" : (s[urlField] || "")).replace(/\/+$/, "");
      if (s[f] && slot && !ks[slot]) ks[slot] = s[f];
    }
    try { localStorage.setItem("apikeys", JSON.stringify(ks)); } catch {}
  }
  CHECKS.forEach(f => $(f).checked = s[f] !== false && s[f] !== "false");
  if (ver < 3 && s.disable_thinking === undefined) $("disable_thinking").checked = true;
  // v4: căn vệt tô mặc định đổi từ 4b trên TUF sang gpt-6-luna qua proxy ChatGPT — đổi cài đặt còn để mặc định cũ
  if (ver < 4 && s.align_preset === "tuf" && s.align_model === "4b") {
    $("align_preset").value = "chatgpt";
    $("align_base_url").value = P("chatgpt").base_url || "";
    $("align_model").value = P("chatgpt").model || "";
  }
  // nhà cung cấp đã lưu không còn (đã xoá) → "Tùy chỉnh", giữ nguyên Base URL và model đang có
  document.querySelectorAll("select.provider-sel").forEach(sel => { if (!sel.value) sel.value = "custom"; });
  fillKeys();
  toggleOpts();
  saveSettings();
}
function saveSettings() {
  const s = { settings_v: 4 };
  FIELDS.forEach(f => s[f] = $(f).value);
  CHECKS.forEach(f => s[f] = $(f).checked);
  try { localStorage.setItem("llm", JSON.stringify(s)); } catch {}
}
function toggleOpts() {
  const eng = $("ocr_engine").value, useLlm = $("translator").value !== "google";
  document.querySelectorAll(".ocr-opt").forEach(el => el.style.display = el.dataset.engine.split(" ").includes(eng) ? "" : "none");
  document.querySelectorAll(".tr-llm").forEach(el => el.style.display = useLlm ? "" : "none");
  document.querySelectorAll(".chat-opt").forEach(el => el.style.display = $("chat_same").checked ? "none" : "");
  document.querySelectorAll(".gloss-opt").forEach(el => el.style.display = $("gloss_same").checked ? "none" : "");
  const sync = $("sync_marks").checked, align = sync && $("align_llm").checked;
  document.querySelectorAll(".sync-on").forEach(el => el.style.display = sync ? "" : "none");
  document.querySelectorAll(".align-on").forEach(el => el.style.display = align ? "" : "none");
  document.querySelectorAll(".align-opt").forEach(el => el.style.display = align && !$("align_same").checked ? "" : "none");
}
["ocr_engine","translator","chat_same","gloss_same","align_llm","align_same","sync_marks"].forEach(f => $(f).addEventListener("change", toggleOpts));

// Bật/tắt đồng bộ vệt tô: vẽ lại mọi trang; bật thì tính nốt phần tương ứng còn thiếu (vệt tô lúc đang tắt)
$("sync_marks").addEventListener("change", () => {
  for (const p of Object.keys(MARKS).map(Number)) {
    paintMarks(p);
    if (syncOn()) { fillPeer(p, "original"); fillPeer(p, "translated"); }
  }
});

function cfg() {
  const same = $("chat_same").checked, gsame = $("gloss_same").checked, asame = $("align_same").checked;
  return { base_url:$("base_url").value, api_key:$("api_key").value, model:$("model").value,
           temperature: parseFloat($("temperature").value) || 0.2, glossary:$("glossary").value,
           glossary_auto: $("glossary_auto").checked,
           translator:$("translator").value, google_fallback:$("google_fallback").checked,
           disable_thinking:$("disable_thinking").checked,
           length_ratio: parseFloat($("length_ratio").value) || 1.3,
           page_span: parseInt($("page_span").value) || 0,
           ocr_mode:$("ocr_mode").value, ocr_engine:$("ocr_engine").value,
           ocr_lang:$("ocr_lang").value || "eng", ocr_api_key:$("ocr_api_key").value,
           ocr_vlm_base_url:$("ocr_vlm_base_url").value, ocr_vlm_api_key:$("ocr_vlm_api_key").value,
           ocr_vlm_model:$("ocr_vlm_model").value, ocr_custom_url:$("ocr_custom_url").value,
           ocr_vlm_concurrency: Math.max(1, parseInt($("ocr_vlm_concurrency").value) || 4),
           ocr_custom_api_key:$("ocr_custom_api_key").value,
           chat_base_url: same ? "" : $("chat_base_url").value,
           chat_api_key: same ? "" : $("chat_api_key").value,
           chat_model: same ? "" : $("chat_model").value,
           gloss_base_url: gsame ? "" : $("gloss_base_url").value,
           gloss_api_key: gsame ? "" : $("gloss_api_key").value,
           gloss_model: gsame ? "" : $("gloss_model").value,
           align_base_url: asame ? "" : $("align_base_url").value,
           align_api_key: asame ? "" : $("align_api_key").value,
           align_model: asame ? "" : $("align_model").value,
           font_serif:$("font_serif").value || "auto", font_sans:$("font_sans").value || "auto",
           font_mono:$("font_mono").value || "auto" };
}
FIELDS.concat(CHECKS).forEach(f => $(f).addEventListener("change", saveSettings));
Object.keys(KEY_OF).forEach(f => $(f).addEventListener("change", () => rememberKey(f)));
// Đổi base URL bằng tay → lấy lại key đã lưu cho server đó
Object.values(KEY_OF).filter(u => u !== "ocrspace").forEach(u => $(u).addEventListener("change", fillKeys));
$("btnSet").onclick = () => $("settings").classList.toggle("open");

// ---------------- Nhà cung cấp
async function loadProviders() {
  try {
    const { providers } = await (await fetch("/api/providers")).json();
    PRESETS = { custom: {} };
    for (const p of providers) PRESETS[p.id] = p;
  } catch {}
  document.querySelectorAll("select.provider-sel").forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = Object.values(PRESETS).filter(p => p.id).map(p =>
      `<option value="${esc(p.id)}">${esc(p.name)}${p.builtin ? "" : " ★"}</option>`).join("") +
      `<option value="custom">Tùy chỉnh (OpenAI-compatible)</option>`;
    if (cur) sel.value = PRESETS[cur] ? cur : "custom";
  });
  drawProviders();
}

// Cùng một khối "nhà cung cấp" cho dịch, OCR (Vision LLM), trò chuyện, thuật ngữ, căn vệt tô
function bindProvider(ids, modelKey) {
  $(ids.preset).addEventListener("change", () => {
    const p = PRESETS[$(ids.preset).value];
    if (p.base_url) $(ids.base).value = p.base_url;
    const m = p[modelKey] || (ids.fallback ? p[ids.fallback] : undefined);
    if (m !== undefined && p.id) $(ids.model).value = m;
    if (ids.parallel && p.parallel) $(ids.parallel).value = p.parallel;
    fillKeys();
    saveSettings();
  });
  document.querySelector(`.psave[data-for="${ids.preset}"]`).onclick = () => quickSaveProvider(ids, modelKey);
  modelPicker(ids);
}

/** 💾 cạnh ô nhà cung cấp: lưu Base URL + model đang nhập. Đang chọn nhà cung cấp của mình thì cập nhật nó (giữ tên),
 *  đổi tên thì lưu thành nhà cung cấp mới. */
async function quickSaveProvider(ids, modelKey) {
  const cur = PRESETS[$(ids.preset).value], own = cur?.id && !cur.builtin;
  const name = prompt(own ? `Cập nhật “${cur.name}” bằng Base URL + model đang nhập?\n(Đổi tên để lưu thành nhà cung cấp mới)`
                          : "Tên nhà cung cấp mới (lưu Base URL + model đang nhập):", own ? cur.name : "");
  if (!name?.trim()) return;
  const same = own && name.trim() === cur.name;
  const body = { ...(same ? cur : { model: "", vision: "", gloss: "", parallel: 1 }), id: same ? cur.id : "",
                 name: name.trim(), base_url: $(ids.base).value, [modelKey]: $(ids.model).value };
  if (ids.parallel) body.parallel = parseInt($(ids.parallel).value) || 1;
  const r = await fetch("/api/providers", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
  if (!r.ok) { alert("Không lưu được: " + ((await r.json().catch(() => ({}))).detail || r.statusText)); return; }
  const saved = await r.json();
  await loadProviders();
  $(ids.preset).value = saved.id;
  saveSettings();
  setStatus(`Đã lưu nhà cung cấp “${saved.name}”`);
}

// ---------------- Ô model: gõ để lọc danh sách model của server (tải khi bấm vào ô, nhớ 24 giờ theo Base URL)
const MODELS = {};                                         // base_url → [{id, name, ctx, vision, free}]
const MODELS_TTL = 24 * 3600 * 1000;
function cachedModels(base) {
  if (MODELS[base]) return MODELS[base];
  try {
    const c = JSON.parse(localStorage.getItem("models:" + base) || "null");
    if (c && Date.now() - c.t < MODELS_TTL && Array.isArray(c.models)) return (MODELS[base] = c.models);
  } catch {}
  return null;
}
async function fetchModels(base, key) {
  const r = await fetch("/api/models", { method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ base_url: base, api_key: key }) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const models = (await r.json()).models.map(m => typeof m === "string" ? { id: m } : m);
  MODELS[base] = models;
  try { localStorage.setItem("models:" + base, JSON.stringify({ t: Date.now(), models })); } catch {}
  loadKeys();
  return models;
}
const recentModels = () => { try { return JSON.parse(localStorage.getItem("recent_models") || "[]"); } catch { return []; } };
function useModel(id) {
  const r = [id, ...recentModels().filter(x => x !== id)].slice(0, 30);
  try { localStorage.setItem("recent_models", JSON.stringify(r)); } catch {}
}
const fmtCtx = n => n >= 1e6 ? Math.round(n / 1e5) / 10 + "M" : Math.round(n / 1024) + "k";
function modelTags(m) {
  return (m.free ? `<i class="free">free</i>` : "") + (m.vision ? `<i class="vis" title="đọc được ảnh">👁 ảnh</i>` : "") +
         (m.ctx ? `<i title="ngữ cảnh">${fmtCtx(m.ctx)}</i>` : "");
}
function modelPicker(ids) {
  const inp = $(ids.model), box = inp.parentElement.querySelector(".mlist");
  let items = [], act = -1, typed = false;
  const base = () => $(ids.base).value.trim().replace(/\/+$/, "");
  const close = () => { box.hidden = true; act = -1; };
  function render(models) {
    const q = typed ? inp.value.trim().toLowerCase().split(/\s+/).filter(Boolean) : [];
    const rec = recentModels(), cur = inp.value;
    let list = models.filter(m => q.every(w => (m.id + " " + (m.name || "")).toLowerCase().includes(w)));
    const rank = m => {
      const id = m.id.toLowerCase(), tail = id.split("/").pop();
      if (!q.length) return m.id === cur ? 0 : rec.includes(m.id) ? 1 + rec.indexOf(m.id) / 100 : 2;
      return tail.startsWith(q[0]) || id.startsWith(q[0]) ? 0 : 1;
    };
    list = list.map(m => [rank(m), m]).sort((a, b) => a[0] - b[0] || a[1].id.localeCompare(b[1].id)).map(x => x[1]);
    items = list.slice(0, 300);
    act = typed && items.length ? 0 : -1;
    box.innerHTML = items.length
      ? items.map((m, i) => `<div class="mopt${m.id === cur ? " cur" : ""}${i === act ? " act" : ""}" data-i="${i}" title="${esc(m.name || m.id)}"><span>${esc(m.id)}</span>${modelTags(m)}</div>`).join("") +
        (list.length > items.length ? `<div class="mhint">… còn ${list.length - items.length} model, gõ thêm để lọc</div>` : "")
      : `<div class="mhint">${models.length ? "Không có model khớp — Enter để dùng đúng tên đã gõ" : "Server không trả về model nào"}</div>`;
    box.hidden = false;
  }
  async function open(force = false) {
    if (!base()) return;
    let models = !force && cachedModels(base());
    if (!models) {
      box.hidden = false;
      box.innerHTML = `<div class="mhint">Đang tải danh sách model…</div>`;
      try { models = await fetchModels(base(), $(ids.key).value); }
      catch (e) { box.innerHTML = `<div class="mhint">Không tải được danh sách model: ${esc(e.message)}</div>`; return; }
      if (document.activeElement !== inp) return;
    }
    render(models);
  }
  function choose(m) {
    inp.value = m.id;
    useModel(m.id);
    close();
    saveSettings();
  }
  function move(k) {
    if (!items.length) return;
    act = (act + k + items.length) % items.length;
    box.querySelectorAll(".mopt").forEach((el, i) => el.classList.toggle("act", i === act));
    box.querySelector(".mopt.act")?.scrollIntoView({ block: "nearest" });
  }
  inp.addEventListener("focus", () => { typed = false; open(); });
  inp.addEventListener("input", () => { typed = true; const m = cachedModels(base()); m ? render(m) : open(); });
  inp.addEventListener("blur", () => setTimeout(close, 150));
  inp.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (box.hidden) open(); else move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); if (!box.hidden && act >= 0) choose(items[act]); else { close(); saveSettings(); } }
    else if (e.key === "Escape" && !box.hidden) { e.stopPropagation(); close(); }
  });
  box.addEventListener("mousedown", e => {
    e.preventDefault();                                    // giữ focus ở ô, không để blur đóng danh sách trước khi chọn
    const el = e.target.closest(".mopt");
    if (el) choose(items[+el.dataset.i]);
  });
  $(ids.btn).onclick = () => { inp.focus(); typed = false; open(true); };
  $(ids.base).addEventListener("change", () => { if (!box.hidden) open(); });
}

// ---------------- Mục "Nhà cung cấp của bạn" trong Cấu hình
let pvEdit = "";                                           // id đang sửa; rỗng = thêm mới
function drawProviders() {
  const own = Object.values(PRESETS).filter(p => p.id && !p.builtin);
  $("pvtab").innerHTML = own.length ? `<tr><th>Tên</th><th>Base URL</th><th>Model</th><th>OCR</th><th>Thuật ngữ</th><th></th></tr>` +
    own.map(p => `<tr data-id="${esc(p.id)}"><td><b>${esc(p.name)}</b></td><td>${esc(p.base_url)}</td><td>${esc(p.model)}</td>
      <td>${esc(p.vision)}</td><td>${esc(p.gloss)}</td>
      <td style="white-space:nowrap"><button type="button" class="pvedit">✏️</button><button type="button" class="pvdel">🗑</button></td></tr>`).join("")
    : `<tr><td class="hint">Chưa có nhà cung cấp nào của bạn.</td></tr>`;
}
function pvForm(p = {}) {
  pvEdit = p.id || "";
  $("pv_name").value = p.name || ""; $("pv_base").value = p.base_url || ""; $("pv_model").value = p.model || "";
  $("pv_vision").value = p.vision || ""; $("pv_gloss").value = p.gloss || ""; $("pv_parallel").value = p.parallel || 1;
  $("pv_key").value = ""; $("pvMsg").textContent = pvEdit ? `đang sửa “${p.name}”` : "";
  $("pvSave").textContent = pvEdit ? "Lưu thay đổi" : "Lưu nhà cung cấp";
}
$("pvtab").onclick = async e => {
  const tr = e.target.closest("tr[data-id]"), p = tr && PRESETS[tr.dataset.id];
  if (!p) return;
  if (e.target.closest(".pvedit")) { pvForm(p); $("pv_name").focus(); }
  if (e.target.closest(".pvdel")) {
    if (!confirm(`Xoá nhà cung cấp “${p.name}”?\nÔ nào đang chọn nó sẽ chuyển sang “Tùy chỉnh”, giữ nguyên Base URL và model.`)) return;
    await fetch(`/api/providers/${encodeURIComponent(p.id)}`, { method:"DELETE" });
    await loadProviders();
    document.querySelectorAll("select.provider-sel").forEach(sel => { if (!PRESETS[sel.value]) sel.value = "custom"; });
    if (pvEdit === p.id) pvForm();
    saveSettings();
  }
};
$("pvNew").onclick = () => pvForm();
$("pvSave").onclick = async () => {
  const base = $("pv_base").value.trim().replace(/\/+$/, ""), key = $("pv_key").value.trim();
  const body = { id: pvEdit, name: $("pv_name").value, base_url: base, model: $("pv_model").value.trim(),
                 vision: $("pv_vision").value.trim(), gloss: $("pv_gloss").value.trim(),
                 parallel: parseInt($("pv_parallel").value) || 1 };
  const r = await fetch("/api/providers", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
  if (!r.ok) { $("pvMsg").textContent = "❌ " + ((await r.json().catch(() => ({}))).detail || r.statusText); return; }
  const saved = await r.json();
  if (key) { const ks = keyStore(); ks[base] = key; try { localStorage.setItem("apikeys", JSON.stringify(ks)); } catch {} }
  await loadProviders();
  pvForm(saved);
  // thử kết nối luôn: biết ngay Base URL/key đúng chưa (và server nhớ key theo Base URL)
  $("pvMsg").textContent = "✓ đã lưu · đang thử kết nối…";
  try { const n = (await fetchModels(base, key)).length; $("pvMsg").textContent = `✓ đã lưu · server trả về ${n} model`; }
  catch (e) { $("pvMsg").textContent = `✓ đã lưu · ⚠️ chưa kết nối được: ${e.message}`; }
  fillKeys();
};

bindProvider({ preset:"preset", base:"base_url", key:"api_key", model:"model", btn:"btnModels" }, "model");
bindProvider({ preset:"ocr_preset", base:"ocr_vlm_base_url", key:"ocr_vlm_api_key", model:"ocr_vlm_model", btn:"btnOcrModels", parallel:"ocr_vlm_concurrency" }, "vision");
bindProvider({ preset:"chat_preset", base:"chat_base_url", key:"chat_api_key", model:"chat_model", btn:"btnChatModels" }, "model");
bindProvider({ preset:"align_preset", base:"align_base_url", key:"align_api_key", model:"align_model", btn:"btnAlignModels" }, "model");
bindProvider({ preset:"gloss_preset", base:"gloss_base_url", key:"gloss_api_key", model:"gloss_model", btn:"btnGlossModels", fallback:"model" }, "gloss");

async function loadKeys() {
  try { SERVER_KEYS = (await (await fetch("/api/keys")).json()).keys || {}; fillKeys(); } catch {}
}
function setStatus(t) { $("status").textContent = t; }

// ================================================================ Mở tài liệu
$("file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  setStatus("Đang tải lên…");
  const fd = new FormData(); fd.append("file", f);
  const r = await fetch("/api/upload", { method:"POST", body:fd });
  e.target.value = "";                        // chọn lại đúng file đó lần nữa vẫn kích hoạt onchange
  if (!r.ok) { setStatus("Lỗi: " + await r.text()); return; }
  const up = await r.json();
  if (up.existing) {                          // file này đã tải lên trước đây: mở lại bản đã lưu, không dịch lại
    await restoreDoc(up.id);
    setStatus($("status").textContent + " · đã mở lại bản lưu trước (cùng file)");
    return;
  }
  doc = up; doc.done = new Set();
  resetDocView();
  openDoc();
  try { localStorage.setItem("last_doc", doc.id); } catch {}
  if ($("glossary_auto").checked && $("translator").value === "llm") loadGlossary(false, true);
};
function resetDocView() {
  window.notesReset?.();                      // ghi chú chung đang chờ lưu được gửi ngay, về đúng bài đã gõ
  MARKS = {}; chatMsgs = []; $("msgs").innerHTML = ""; dropSel(); GLOSS = []; EXTRA = [];
  $("gloss").classList.remove("open"); $("gtab").innerHTML = ""; $("gextra").innerHTML = ""; $("gcount").textContent = "";
}

function openDoc() {
  buildPages();
  ["btnAll","btnDl","btnDlOrg","btnGloss","btnChat","btnNotes","range"].forEach(b => $(b).disabled = false);
  setStatus(`${doc.name} – ${doc.pages} trang` + (doc.scanned_pages ? ` (${doc.scanned_pages} trang scan → OCR)` : "") +
            (doc.done.size ? ` · đã dịch ${doc.done.size}` : ""));
}

// Mở một tài liệu đã lưu trên server (bản dịch, tô vàng, bảng thuật ngữ lưu trên đĩa — khởi động lại app vẫn còn).
// Không có id: tài liệu mở gần nhất (tải lại trang là thấy lại đúng bài đang đọc).
async function restoreDoc(id) {
  const remembered = !id;
  if (!id) try { id = localStorage.getItem("last_doc"); } catch {}
  if (!id) return false;
  const r = await fetch(`/api/${id}/state`);
  if (!r.ok) {                                // bài nhớ lần trước đã bị xoá → quên đi, khỏi hỏi lại mỗi lần mở app
    if (remembered && r.status === 404) try { localStorage.removeItem("last_doc"); } catch {}
    return false;
  }
  const st = await r.json();
  stopFlag = true;                            // đang "Dịch tất cả" bài khác thì dừng, đừng dịch lẫn sang bài này
  doc = { id, pages: st.pages, name: st.name, scanned_pages: 0, done: new Set(st.done) };
  resetDocView();
  try { localStorage.setItem("last_doc", id); } catch {}
  for (const [p, ms] of Object.entries(st.marks)) MARKS[+p] = ms;
  GLOSS = st.terms || []; EXTRA = st.extra || [];
  openDoc();
  if (GLOSS.length) drawGlossary();
  window.notesLoad?.(st.notes);
  for (const p of st.done) showTranslated({ page: p, cached: true }, {});
  return true;
}

// ================================================================ Tài liệu đã lưu
$("btnDocs").onclick = () => { $("docs").classList.toggle("open"); if ($("docs").classList.contains("open")) loadDocs(); };
$("dClose").onclick = () => $("docs").classList.remove("open");
const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.round(b / 1024) + " KB";
const fmtTime = t => new Date(t * 1000).toLocaleString("vi-VN", { dateStyle:"short", timeStyle:"short" });
async function loadDocs() {
  $("dcount").textContent = "đang tải…";
  let list = [];
  try { list = (await (await fetch("/api/docs")).json()).docs; } catch (e) { $("dcount").textContent = "lỗi: " + e.message; return; }
  $("dcount").textContent = list.length ? `${list.length} tài liệu · ${fmtSize(list.reduce((a, d) => a + d.size, 0))}` : "chưa có tài liệu nào";
  $("dtab").innerHTML = list.length ? `<tr><th>Tài liệu</th><th>Đã dịch</th><th>Tô vàng</th><th>Ghi chú</th><th>Lần cuối</th><th>Dung lượng</th><th></th></tr>` +
    list.map(d => `<tr data-id="${esc(d.id)}" class="${doc?.id === d.id ? "cur" : ""}">
      <td><a href="#" class="dopen"><b>${esc(d.title || d.name)}</b></a>${d.title ? `<div class="hint">${esc(d.name)}</div>` : ""}</td>
      <td>${d.done}/${d.pages} trang</td><td>${d.marks || ""}</td><td>${d.notes || ""}</td><td>${fmtTime(d.updated)}</td><td>${fmtSize(d.size)}</td>
      <td><button class="ddel" title="Xoá hẳn bản gốc, bản dịch, tô vàng, thuật ngữ của tài liệu này">🗑</button></td></tr>`).join("") : "";
}
$("dtab").onclick = async e => {
  const tr = e.target.closest("tr[data-id]"); if (!tr) return;
  const id = tr.dataset.id, name = tr.querySelector("b").textContent;
  if (e.target.closest(".dopen")) {
    e.preventDefault();
    if (await restoreDoc(id)) $("docs").classList.remove("open"); else setStatus("Không mở được tài liệu này");
  } else if (e.target.closest(".ddel")) {
    if (!confirm(`Xoá hẳn “${name}”?\nBản dịch, tô vàng và bảng thuật ngữ của tài liệu này sẽ mất, không hoàn tác được.`)) return;
    await fetch(`/api/docs/${id}`, { method:"DELETE" });
    if (doc?.id === id) {                     // xoá đúng bài đang mở → về màn hình trống
      doc = null; resetDocView(); $("cols").innerHTML = ""; setStatus("Chưa mở file");
      ["btnAll","btnDl","btnDlOrg","btnGloss","btnChat","btnNotes","range"].forEach(b => $(b).disabled = true);
      try { localStorage.removeItem("last_doc"); } catch {}
    }
    loadDocs();
  }
};

function pageBox(i, kind, inner) {
  return `<div class="page" data-p="${i}" data-kind="${kind}"><span class="num">${i+1}</span>${inner}<div class="layer"></div></div>`;
}
function buildPages() {
  const cols = $("cols");
  cols.innerHTML = `<div class="colhead">Bản gốc</div><div class="colhead">Bản dịch tiếng Việt</div>`;
  for (let i = 0; i < doc.pages; i++) {
    cols.insertAdjacentHTML("beforeend",
      pageBox(i, "original", `<img loading="lazy" src="/api/${doc.id}/page/${i}/original.png">`) +
      pageBox(i, "translated", `<div class="ph"><span>Chưa dịch</span><button onclick="translatePages([${i}])">Dịch trang này</button></div>`));
  }
  cols.querySelectorAll(".page").forEach(el => { RO.observe(el); IO.observe(el); });
  window.viewLayout?.();
}

// ================================================================ Lớp chữ: bôi đen chọn chữ, tô vàng theo đoạn
// Lớp chữ dựng theo đơn vị của PDF (1px = 1pt) rồi thu cả lớp bằng transform scale, nên chỉ cần tính một lần
// và tự khớp khi đổi bề rộng cửa sổ.
const RO = new ResizeObserver(es => es.forEach(e => fitLayer(e.target)));
const IO = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) loadLayer(e.target); }),
                                    { rootMargin: "400px" });
function fitLayer(el) {
  const layer = el.querySelector(".layer"), w = +layer?.dataset.w;
  if (w) layer.style.transform = `scale(${el.clientWidth / w})`;
}
async function loadLayer(el, force = false) {
  if (!doc || (el.dataset.loaded === "1" && !force)) return;
  const p = +el.dataset.p, kind = el.dataset.kind;
  if (kind === "translated" && !doc.done.has(p)) return;
  el.dataset.loaded = "1";
  try {
    const data = await (await fetch(`/api/${doc.id}/page/${p}/layer?kind=${kind}`)).json();
    if (!data.ready || !data.lines.length) { el.dataset.loaded = ""; return; }
    buildLayer(el, data);
  } catch { el.dataset.loaded = ""; }
}
function buildLayer(el, data) {
  const layer = el.querySelector(".layer"), frag = document.createDocumentFragment(), spans = [];
  layer.innerHTML = "";
  layer.dataset.w = data.w;
  layer.style.width = data.w + "px";
  layer.style.height = data.h + "px";
  for (const [i, r] of Object.entries(data.blocks)) {
    const b = document.createElement("div");
    b.className = "blk"; b.dataset.b = i;
    b.style.cssText = `left:${r[0]-2}px;top:${r[1]-2}px;width:${r[2]-r[0]+4}px;height:${r[3]-r[1]+4}px`;
    frag.appendChild(b);
  }
  for (const ln of data.lines) {
    const s = document.createElement("span"), h = Math.max(ln.bbox[3] - ln.bbox[1], 1);
    s.className = "ln"; s.textContent = ln.text;
    if (ln.b !== null && ln.b !== undefined) s.dataset.b = ln.b;
    if (ln.j) s.dataset.j = "1";
    s.style.cssText = `left:${ln.bbox[0]}px;top:${ln.bbox[1]}px;height:${h}px;line-height:${h}px;font-size:${ln.size}px`;
    frag.appendChild(s);
    spans.push([s, ln.bbox[2] - ln.bbox[0]]);
  }
  layer.appendChild(frag);
  // Kéo giãn ngang mỗi dòng cho đúng bề rộng dòng gốc (font trình duyệt khác font PDF) → vệt bôi đen khớp chữ
  for (const [s, w] of spans) { const own = s.offsetWidth; if (own > 1) s.style.transform = `scaleX(${w / own})`; }
  fitLayer(el);
  paintMarks(+el.dataset.p);
  // tô bù sang bản kia CẢ HAI chiều: cần chữ của cả hai bản, và bản nào nạp xong trước là tuỳ lúc — trước đây chỉ
  // gọi theo chiều của lớp vừa dựng, nên bản dịch nạp trước thì vệt tô ở bản gốc không bao giờ hiện bên dịch
  fillPeer(+el.dataset.p, "original");
  fillPeer(+el.dataset.p, "translated");
}

// Rê chuột lên một đoạn → đoạn tương ứng ở bản kia sáng mờ (số hiệu đoạn giống nhau ở hai bản)
$("cols").addEventListener("mouseover", e => {
  const ln = e.target.closest?.(".ln");
  document.querySelectorAll(".blk.peer").forEach(b => b.classList.remove("peer"));
  if (!ln || ln.dataset.b === undefined) return;
  const pg = ln.closest(".page");
  pageOf(+pg.dataset.p, otherSide(pg.dataset.kind))
    ?.querySelector(`.blk[data-b="${ln.dataset.b}"]`)?.classList.add("peer");
});

// ---------------- Tô vàng (đúng phần chữ đã bôi đen, đồng bộ 2 bản, lưu ở server, có cả trong PDF tải về)
// Vệt tô được lưu theo VỊ TRÍ KÝ TỰ trong chữ của đoạn, kèm các khung chữ (đơn vị PDF) để vẽ lại. Sang bản kia
// thì khớp theo CÂU: chữ hai bản khác nhau nên không khớp được từng ký tự, nhưng trong một đoạn thì thứ tự câu
// giữ nguyên (prompt cấm model gộp/tách đoạn). Số câu hai bên lệch nhau thì mới tô cả đoạn.
let MARKS = {};
// Đồng bộ vệt tô: bật thì tô ở bản này là bản kia sáng phần tương ứng (`peer`); tắt thì hai bản tô độc lập.
// Tắt không xoá `peer` đã có: chỉ không vẽ, không tính mới — bật lại là thấy lại.
const syncOn = () => $("sync_marks").checked;
const pageOf = (p, kind) => document.querySelector(`.page[data-p="${p}"][data-kind="${kind}"]`);
const otherSide = k => (k === "original" ? "translated" : "original");
const rid = () => Math.random().toString(36).slice(2, 10);

/** Chữ của một đoạn, ghép từ các dòng, kèm vị trí bắt đầu của từng dòng trong chuỗi đó. */
function blockText(pageEl, b) {
  const map = [];
  let text = "";
  for (const sp of pageEl.querySelectorAll(`.ln[data-b="${b}"]`)) {
    const t = sp.textContent;
    // nối dòng bằng một dấu cách, trừ mẩu có data-j (phần còn lại của dòng bị cắt ra để chen công thức
    // vào giữa: "nhỏ hơn" + "i" + "." phải ra "nhỏ hơn i." chứ không phải "nhỏ hơn i .")
    if (text && !/\s$/.test(text) && sp.dataset.j !== "1") text += " ";
    map.push({ sp, at: text.length, len: t.length });
    text += t;
  }
  return { text, map };
}

/** Vùng chọn → [đầu, cuối) tính theo ký tự trong chữ của đoạn. */
function rangeOffsets(range, map) {
  let s = null, e = null;
  for (const m of map) {
    if (!m.sp.firstChild || !range.intersectsNode(m.sp)) continue;
    const node = m.sp.firstChild;
    const a = range.startContainer === node ? range.startOffset : 0;
    const z = range.endContainer === node ? range.endOffset : m.len;
    if (z <= a) continue;
    if (s === null) s = m.at + a;
    e = m.at + z;
  }
  return s === null ? null : [s, e];
}

/** [đầu, cuối) → các khung chữ theo toạ độ PDF của trang (dùng để vẽ và để ghi vào PDF tải về). */
function offsetRects(pageEl, b, s, e) {
  const layer = pageEl.querySelector(".layer");
  if (!layer || !layer.offsetWidth) return [];
  const lr = layer.getBoundingClientRect(), k = lr.width / layer.offsetWidth;
  const out = [];
  for (const m of blockText(pageEl, b).map) {
    const a = Math.max(s - m.at, 0), z = Math.min(e - m.at, m.len);
    if (z <= a || !m.sp.firstChild) continue;
    const r = document.createRange();
    r.setStart(m.sp.firstChild, a);
    r.setEnd(m.sp.firstChild, z);
    for (const c of r.getClientRects()) {
      if (c.width > 0.5) out.push([(c.left-lr.left)/k, (c.top-lr.top)/k, (c.right-lr.left)/k, (c.bottom-lr.top)/k]);
    }
  }
  return out;
}

const SENT_END = /[.!?…]["'”’)\]]*(?=\s|$)|[。！？]/g;
// Dấu chấm KHÔNG kết câu: viết tắt học thuật và chữ cái đầu của tên người ("J. Smith", "Fig.", "et al.")
const NO_SPLIT = /(?:^|[\s(])(?:[A-Za-z]|et al|e\.g|i\.e|cf|vs|Fig|Eq|Sec|Tab|No|Dr|Mr|Ms|Prof|Ref)\.$/;

function sentSpans(text) {
  const out = [];
  let at = 0;
  SENT_END.lastIndex = 0;
  for (let m; (m = SENT_END.exec(text)); ) {
    const end = m.index + m[0].length;
    if (NO_SPLIT.test(text.slice(Math.max(0, m.index - 10), m.index + 1))) continue;
    out.push([at, end]);
    at = end;
  }
  if (at < text.length) out.push([at, text.length]);
  // Mẩu không có chữ cái nào (dấu chú thích † ‡ ∗ đứng lẻ — bản dịch hay đẩy xuống cuối) thì nhập vào câu trước:
  // để riêng thì số câu hai bản lệch nhau và phải lùi về tô cả đoạn.
  for (let i = out.length - 1; i > 0; i--) {
    if (!/\p{L}/u.test(text.slice(out[i][0], out[i][1]))) {
      out[i - 1][1] = out[i][1];
      out.splice(i, 1);
    }
  }
  return out.length ? out : [[0, text.length]];
}

/** Đoạn chữ tương ứng ở bản kia: các câu mà vùng chọn chạm tới; lệch số câu thì lấy cả đoạn.
 *  Trả thêm `ctx` = [đầu, cuối) của các câu đó ở bản này, và `exact` = số câu hai bản khớp nhau. */
function peerRange(srcText, dstText, s, e) {
  const A = sentSpans(srcText), B = sentSpans(dstText);
  const hit = A.map((r, i) => i).filter(i => A[i][1] > s && A[i][0] < e);
  const ctx = hit.length ? [A[hit[0]][0], A[hit[hit.length - 1]][1]] : [0, srcText.length];
  if (!hit.length || A.length !== B.length) return Object.assign([0, dstText.length], { ctx, exact: false });
  return Object.assign([B[hit[0]][0], B[hit[hit.length - 1]][1]], { ctx, exact: true });
}

// ---------------- Căn bằng LLM: tô NGAY cả câu tương ứng (nhạt, viền đứt), LLM trả lời thì thu về đúng cụm.
// Server kiểm cụm LLM trả về phải có thật trong câu bên kia; không có thì giữ nguyên cả câu (không bao giờ tô bậy).
const WAIT = new Set();       // id các vệt đang chờ LLM
/** Có cần hỏi LLM không: bôi đen gần hết các câu mà số câu hai bản khớp nhau thì cả câu đã là đáp án đúng. */
function needAlign(srcText, m, pr) {
  if (!syncOn() || !$("align_llm").checked) return false;
  const [cs, ce] = pr.ctx, letters = t => (t.match(/[\p{L}\p{N}]/gu) || []).length;
  return !pr.exact || letters(srcText.slice(m.s, m.e)) < 0.9 * letters(srcText.slice(cs, ce));
}
async function alignMark(p, m, srcText, dstText, pr) {
  const [cs, ce] = pr.ctx, [ps, pe] = pr, mine = doc;
  WAIT.add(m.id);
  let res = null;
  try {
    const r = await fetch(`/api/${doc.id}/align`, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ cfg: cfg(), src: srcText.slice(cs, ce), s: m.s - cs, e: m.e - cs, dst: dstText.slice(ps, pe) }) });
    if (r.ok) res = await r.json();
  } catch {}
  WAIT.delete(m.id);
  const cur = doc === mine && (MARKS[p] || []).find(x => x.id === m.id);  // vệt đã bị bỏ trong lúc chờ → thôi
  if (!cur || cur.side !== m.side) return;   // hoặc đã chuyển sang neo ở bản gốc (dịch lại trang, xem reanchorMarks)
  const el = pageOf(p, otherSide(m.side));
  if (res?.s != null && el) {
    const rects = offsetRects(el, m.block, ps + res.s, ps + res.e);
    if (rects.length) {
      Object.assign(cur, { ps: ps + res.s, pe: ps + res.e, peer: rects, via: "llm" });
      await sendMarks(p, [cur], [cur.id]);
      return;
    }
  }
  if (res && res.s == null) setStatus("Căn vệt tô: " + res.reason + " → giữ tô cả câu");
  paintMarks(p);
}

function paintMarks(p) {
  for (const kind of ["original", "translated"]) {
    const el = pageOf(p, kind), layer = el?.querySelector(".layer");
    if (!layer) continue;
    layer.querySelectorAll(".hl").forEach(x => x.remove());
    for (const m of MARKS[p] || []) {
      const wait = m.side !== kind && WAIT.has(m.id);
      for (const r of (m.side === kind ? m.rects : syncOn() ? m.peer : []) || []) {
        const d = document.createElement("div");
        d.className = wait ? "hl wait" : "hl";
        d.style.cssText = `left:${r[0]}px;top:${r[1]}px;width:${r[2]-r[0]}px;height:${r[3]-r[1]}px`;
        layer.appendChild(d);
      }
    }
  }
  window.paintNoteDots?.(p);                  // dấu 📝 trên vệt tô có ghi chú (notes.js)
}

/** Bản kia mới dựng xong lớp chữ → tính nốt khung tương ứng cho các vệt tô đã có. */
async function fillPeer(p, kind) {
  const el = pageOf(p, kind), src = pageOf(p, otherSide(kind));
  const todo = (MARKS[p] || []).filter(m => m.side !== kind && !m.peer.length);
  if (!syncOn() || !el || !src || !todo.length) return;
  const add = [], later = [];
  for (const m of todo) {
    const a = blockText(src, m.block), b = blockText(el, m.block);
    if (!a.text || !b.text) continue;
    const pr = peerRange(a.text, b.text, m.s, m.e);
    [m.ps, m.pe] = pr;
    m.peer = offsetRects(el, m.block, m.ps, m.pe);
    if (!m.peer.length) continue;
    add.push(m);
    if (needAlign(a.text, m, pr)) { WAIT.add(m.id); later.push([m, a.text, b.text, pr]); }
  }
  if (add.length) {
    await fetch(`/api/${doc.id}/marks`, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ page: p, add, remove: add.map(m => m.id) }) });
    paintMarks(p);
    later.forEach(([m, a, b, pr]) => alignMark(p, m, a, b, pr));
  }
}

/** Vùng chữ mà vệt tô `m` chiếm TRÊN BẢN `side`; null nghĩa là bên đó chưa vẽ được vệt này. */
function markRange(m, side) {
  if (m.side === side) return [m.s, m.e];
  return syncOn() && m.peer?.length ? [m.ps, m.pe] : null;
}

/** Các vệt tô mà vùng chọn hiện tại đang đè lên (để đổi nút thành "Bỏ tô vàng"). */
function marksHit(info) {
  const el = pageOf(info.page, info.side);
  const off = info.blocks.length ? rangeOffsets(info.range, blockText(el, info.blocks[0]).map) : null;
  return (MARKS[info.page] || []).filter(m => {
    if (!info.blocks.includes(m.block)) return false;
    const r = markRange(m, info.side);
    // phải tính vị trí ký tự trong ĐÚNG đoạn của vệt tô đó (vùng chọn có thể trải qua nhiều đoạn)
    const o = m.block === info.blocks[0] ? off : rangeOffsets(info.range, blockText(el, m.block).map);
    return r && o && o[0] < r[1] && r[0] < o[1];
  });
}

async function addMarks(info) {
  const el = pageOf(info.page, info.side), peer = pageOf(info.page, otherSide(info.side));
  const add = [], later = [];
  for (const b of info.blocks) {
    const src = blockText(el, b);
    const off = rangeOffsets(info.range, src.map);
    if (!off) continue;
    const m = { id: rid(), block: b, side: info.side, s: off[0], e: off[1],
                text: src.text.slice(off[0], off[1]).trim(),
                rects: offsetRects(el, b, off[0], off[1]), peer: [], ps: 0, pe: 0, via: "" };
    const dst = syncOn() && peer && peer.dataset.loaded === "1" ? blockText(peer, b) : null;
    if (dst?.text) {
      const pr = peerRange(src.text, dst.text, off[0], off[1]);
      [m.ps, m.pe] = pr;
      m.peer = offsetRects(peer, b, m.ps, m.pe);
      if (m.peer.length && needAlign(src.text, m, pr)) { WAIT.add(m.id); later.push([m, src.text, dst.text, pr]); }
    }
    if (m.rects.length) add.push(m);
  }
  if (!add.length) return [];
  await sendMarks(info.page, add, []);                 // tô ngay cả câu, không chờ LLM
  later.forEach(([m, a, b, pr]) => alignMark(info.page, m, a, b, pr));
  return add;                                          // nút 📝 Ghi chú gắn ghi chú vào vệt vừa tô
}

async function sendMarks(page, add, remove) {
  const r = await fetch(`/api/${doc.id}/marks`, { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ page, add, remove }) });
  if (!r.ok) {   // tài liệu không còn trên server (app đã khởi động lại) → nói ra thay vì im lặng mất vệt tô
    setStatus("Không lưu được tô vàng: tài liệu không còn trên server, hãy mở lại file");
    return;
  }
  MARKS[page] = (await r.json()).marks;
  paintMarks(page);
  window.notesMarksChanged?.(page);
}

// ---------------- Bôi đen → thanh nổi
let sel = null;
function selectionInfo() {
  const s = window.getSelection();
  if (!s || s.isCollapsed) return null;
  const text = s.toString().trim();
  if (!text) return null;
  const node = s.anchorNode?.nodeType === 3 ? s.anchorNode.parentElement : s.anchorNode;
  const page = node?.closest?.(".page");
  if (!page) return null;
  const blocks = new Set();
  page.querySelectorAll(".ln").forEach(el => {
    if (el.dataset.b !== undefined && s.containsNode(el, true)) blocks.add(+el.dataset.b);
  });
  const range = s.getRangeAt(0);
  return { text, page: +page.dataset.p, side: page.dataset.kind, blocks: [...blocks],
           range, rect: range.getBoundingClientRect() };
}
document.addEventListener("selectionchange", () => {
  const info = selectionInfo(), bar = $("selbar");
  if (!info) { bar.classList.remove("on"); return; }
  sel = info;
  $("selMark").textContent = marksHit(info).length ? "🖍 Bỏ tô vàng" : "🖍 Tô vàng";
  $("selMark").disabled = !info.blocks.length;
  bar.classList.add("on");
  bar.style.left = Math.max(8, info.rect.left + window.scrollX) + "px";
  bar.style.top = (info.rect.bottom + window.scrollY + 6) + "px";
});
$("selMark").onclick = async () => {
  const info = sel, hit = marksHit(info);
  $("selbar").classList.remove("on");
  if (hit.length) await sendMarks(info.page, [], hit.map(m => m.id));
  else await addMarks(info);
  window.getSelection().removeAllRanges();
};
$("selAsk").onclick = () => {
  keepSel(sel);
  $("selbar").classList.remove("on");
  openChat();
  $("q").value = $("q").value || "Giải thích đoạn này";
  $("q").focus();
};

// ================================================================ Dịch
function busyBox(i, n) {
  return `<span class="num">${i+1}</span><div class="ph">⏳ Đang dịch trang ${i+1}${n > 1 ? ` (nhóm ${n} trang)` : ""}…</div>`;
}
function idleBox(i) {
  return `<span class="num">${i+1}</span><div class="ph"><span>Chưa dịch</span>
    <button onclick="translatePages([${i}])">Dịch trang này</button></div><div class="layer"></div>`;
}
const trBox = i => document.querySelector(`.page[data-p="${i}"][data-kind="translated"]`);

/** Trước khi dịch lại một trang: vệt tô ở bản dịch neo theo vị trí ký tự trong chữ bản dịch CŨ, dịch xong chữ đổi
 *  thì vệt lệch sang chỗ khác. Chuyển neo của chúng sang phần tương ứng ở bản gốc (đã căn bằng LLM nếu có, không
 *  thì câu tương ứng) — bản gốc không đổi; dịch xong fillPeer tính lại phần tương ứng trong bản dịch MỚI (và hỏi LLM
 *  đúng cụm nếu bật). Id giữ nguyên nên ghi chú gắn với vệt vẫn theo vệt. */
async function reanchorMarks(p) {
  const ms = (MARKS[p] || []).filter(m => m.side === "translated");
  const tr = pageOf(p, "translated"), org = pageOf(p, "original");
  if (!ms.length || !org) return;
  await loadLayer(org);
  const add = [];
  for (const m of ms) {
    const a = blockText(org, m.block);
    let [s, e] = [m.ps, m.pe];
    if (!m.peer?.length) {            // chưa có phần tương ứng ở bản gốc → tính ngay từ chữ hai bản
      const b = tr?.dataset.loaded === "1" ? blockText(tr, m.block) : null;
      if (!a.text || !b?.text) continue;
      [s, e] = peerRange(b.text, a.text, m.s, m.e);
    }
    const rects = offsetRects(org, m.block, s, e);
    if (!rects.length) continue;
    WAIT.delete(m.id);
    add.push({ ...m, side: "original", s, e, text: a.text.slice(s, e).trim(), rects, peer: [], ps: 0, pe: 0, via: "" });
  }
  if (add.length) {
    await sendMarks(p, add, add.map(m => m.id));
    if (!syncOn()) setStatus(`Trang ${p + 1}: ${add.length} vệt tô ở bản dịch đã chuyển sang câu tương ứng ở bản gốc (bản dịch sắp đổi chữ)`);
  }
}

async function translatePages(pages, force = false) {
  if (force) for (const i of pages) await reanchorMarks(i);
  pages.forEach(i => { trBox(i).innerHTML = busyBox(i, pages.length); trBox(i).dataset.loaded = ""; });
  const mine = doc;           // trong lúc chờ mà người dùng mở tài liệu khác: bỏ kết quả, đừng vẽ nhầm sang bài kia
  let res;
  try {
    const r = await fetch(`/api/${doc.id}/translate/${pages[0]}?force=${force}&span=${pages.length}`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(cfg()) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    res = await r.json();
  } catch (e) {
    if (doc !== mine) return [];
    trBox(pages[0]).innerHTML = `<span class="num">${pages[0]+1}</span><div class="ph">
      <span style="color:#b91c1c">❌ ${e.message}</span>
      <button onclick="translatePages([${pages[0]}])">Thử lại</button></div><div class="layer"></div>`;
    pages.slice(1).forEach(restoreBox);
    throw e;
  }
  if (doc !== mine) return [];
  if (force) {                // dịch lại: bản dịch đổi chữ và bố cục → tính lại khung bên dịch của các vệt tô ở bản gốc
    for (const pg of res.pages) {
      for (const m of MARKS[pg.page] || []) if (m.side === "original") Object.assign(m, { peer: [], ps: 0, pe: 0, via: "" });
    }
  }
  const done = [];
  for (const pg of res.pages) {
    doc.done.add(pg.page);
    done.push(pg.page);
    showTranslated(pg, res);
  }
  pages.filter(i => !done.includes(i)).forEach(restoreBox);
  return done;
}
/** Trang đã giữ chỗ mà lượt này không dịch tới: về lại như trước (bản dịch cũ nếu đã dịch, không thì "Chưa dịch"). */
function restoreBox(i) {
  if (doc.done.has(i)) showTranslated({ page: i, cached: true }, {});
  else trBox(i).innerHTML = idleBox(i);
}

function showTranslated(pg, res) {
  const i = pg.page, box = trBox(i);
  if (pg.cached) { box.innerHTML = `<span class="num">${i+1}</span>
      <div class="tools"><button onclick="translatePages([${i}], true)">↻ Dịch lại</button></div>
      <img src="/api/${doc.id}/page/${i}/translated.png?t=${Date.now()}"><div class="layer"></div>`; }
  else {
    const tags = [pg.ocr && "OCR", res.group > 1 && `nhóm ${res.group} trang`,
                  res.via_google && `Google: ${res.via_google}`, res.errors?.length && "⚠️ LLM lỗi"].filter(Boolean);
    const t = res.tokens || {};
    const title = `${pg.translated}/${pg.blocks} đoạn đã dịch` +
      (t.calls ? ` · cả nhóm: ${t.calls} lượt gọi, ${(t.prompt||0)+(t.completion||0)} token (lượt lớn nhất ${t.max_call}/${t.num_ctx})` : "");
    box.innerHTML = `<span class="num">${i+1}</span>
      <div class="tools">${tags.map(x => `<button disabled title="${(res.errors||[]).join(" ").replace(/"/g,"'")}">${x}</button>`).join("")}<button onclick="translatePages([${i}], true)">↻ Dịch lại</button></div>
      <img src="/api/${doc.id}/page/${i}/translated.png?t=${Date.now()}" title="${title}"><div class="layer"></div>`;
  }
  box.dataset.loaded = "";
  loadLayer(box);
  loadLayer(document.querySelector(`.page[data-p="${i}"][data-kind="original"]`), true);
}

/** "1, 3, 5, 9 - 13", "10-" (tới hết), "-5" (từ đầu) → số trang (đếm từ 0), đã sắp xếp, không trùng.
 *  Trống = cả bài. Sai cú pháp hay vượt số trang → ném lỗi có lời nhắn cho người dùng. */
function parseRange(str, pages) {
  const out = new Set(), s = str.replace(/[–—]/g, "-").replace(/\s*-\s*/g, "-").trim();   // "9 - 13" → "9-13"
  if (!s) return [...Array(pages).keys()];
  for (const tok of s.split(/[,;\s]+/).filter(Boolean)) {
    const m = tok.match(/^(\d*)-(\d*)$|^(\d+)$/);
    if (!m || (m[1] === "" && m[2] === "" && !m[3])) throw new Error(`không hiểu “${tok}”`);
    let a = m[3] ? +m[3] : m[1] === "" ? 1 : +m[1], b = m[3] ? +m[3] : m[2] === "" ? pages : +m[2];
    if (a > b) [a, b] = [b, a];
    if (a < 1 || b > pages) throw new Error(`trang ${a < 1 ? a : b} ngoài khoảng 1–${pages}`);
    for (let p = a; p <= b; p++) out.add(p - 1);
  }
  return [...out].sort((x, y) => x - y);
}
$("range").addEventListener("input", () => {
  if (!doc) return;
  try { parseRange($("range").value, doc.pages); $("range").classList.remove("bad"); $("range").title = ""; }
  catch (e) { $("range").classList.add("bad"); $("range").title = e.message; }
});
$("range").addEventListener("keydown", e => { if (e.key === "Enter" && !$("btnAll").disabled) $("btnAll").click(); });

$("btnAll").onclick = async () => {
  let want;
  try { want = parseRange($("range").value, doc.pages); }
  catch (e) { $("range").classList.add("bad"); setStatus("Dải trang sai: " + e.message); return; }
  const force = $("rangeForce").checked;
  stopFlag = false; $("btnAll").disabled = true; $("btnStop").disabled = false;
  const queue = want.filter(i => force || !doc.done.has(i));
  const total = queue.length, left = new Set(queue); let errors = 0;
  const update = () => { const k = total - left.size;
                         $("bar").style.width = (total ? k / total * 100 : 100) + "%";
                         setStatus(`Đã dịch ${k}/${total} trang${total < doc.pages ? " đã chọn" : ""}` +
                                   `${errors ? `, ${errors} lỗi` : ""} · cả bài ${doc.done.size}/${doc.pages}`); };
  update();
  const span = () => parseInt($("page_span").value) || 3;   // "tự động": giữ chỗ 3 trang, server gộp được bao nhiêu thì gộp
  const worker = async () => {
    while (queue.length && !stopFlag) {
      const take = [queue.shift()];   // giữ chỗ các trang liền nhau để hai worker không giành nhau cùng một trang
      while (take.length < span() && queue.length && queue[0] === take[take.length - 1] + 1) take.push(queue.shift());
      try {
        const got = await translatePages(take, force);
        got.forEach(i => left.delete(i));
        queue.unshift(...take.filter(i => !got.includes(i)));   // server gộp ít hơn → trả lại hàng đợi
      } catch {
        errors++;
        left.delete(take[0]);
        queue.unshift(...take.slice(1));
      }
      update();
    }
  };
  const n = Math.max(1, parseInt($("concurrency").value) || 1);
  await Promise.all(Array.from({ length:n }, worker));
  $("btnAll").disabled = false; $("btnStop").disabled = true;
};
$("btnStop").onclick = () => { stopFlag = true; setStatus("Đang dừng sau trang hiện tại…"); };
$("btnDl").onclick = () => { location.href = `/api/${doc.id}/download?sync=${syncOn()}`; };
$("btnDlOrg").onclick = () => { location.href = `/api/${doc.id}/download?which=original&sync=${syncOn()}`; };

// ================================================================ Bảng thuật ngữ
let GLOSS = [], EXTRA = [];   // EXTRA: ứng viên LLM không chọn + thuật ngữ bị ẩn vì bạn từng xoá (hidden)
$("btnGloss").onclick = () => { $("gloss").classList.toggle("open"); if (!GLOSS.length) loadGlossary(false); };
$("gClose").onclick = () => $("gloss").classList.remove("open");
$("gMake").onclick = () => loadGlossary(true);
$("gAdd").onclick = () => {
  $("gFind").value = "";                      // dòng mới trống: bộ lọc đang bật sẽ ẩn mất nó
  GLOSS.unshift({ term: "", vi: "", keep: false, use: true });
  drawGlossary();
  $("gtab").querySelector(".gterm")?.focus();
};

async function loadGlossary(force, quiet = false) {
  if (!doc) return;
  $("gcount").textContent = "đang hỏi LLM để lập bảng…";
  if (!quiet) $("gloss").classList.add("open");
  try {
    const r = await fetch(`/api/${doc.id}/glossary?force=${!!force}`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(cfg()) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const j = await r.json();
    GLOSS = j.terms || []; EXTRA = j.extra || [];
    drawGlossary();
  } catch (e) {
    $("gcount").textContent = "không lập được bảng: " + e.message;
  }
}
function drawGlossary() {
  const rows = GLOSS.map((t, i) => `<tr data-i="${i}" class="${t.use ? "" : "off"}">
      <td><input type="checkbox" class="guse" ${t.use ? "checked" : ""} title="Bỏ tích: không đưa thuật ngữ này vào prompt"></td>
      <td><input type="text" class="gterm" value="${esc(t.term)}" placeholder="thuật ngữ trong bài"></td>
      <td><label><input type="radio" name="g${i}" class="gmode" value="vi" ${t.keep ? "" : "checked"}> dịch</label>
          <label><input type="radio" name="g${i}" class="gmode" value="keep" ${t.keep ? "checked" : ""}> giữ nguyên</label></td>
      <td><input type="text" class="gvi" value="${esc(t.vi)}" ${t.keep ? "disabled" : ""}></td>
      <td><button type="button" class="gdel" title="Xoá khỏi bảng. Sau khi Lưu bảng, app nhớ lại: tài liệu sau sẽ không gợi ý thuật ngữ này nữa">✕</button></td></tr>`).join("");
  $("gtab").innerHTML = `<tr><th>Dùng</th><th>Thuật ngữ trong bài</th><th>Cách xử lý</th><th>Dịch là</th><th></th></tr>${rows}`;
  countGloss();
  $("gtab").onclick = e => {
    if (!e.target.classList.contains("gdel")) return;
    GLOSS.splice(+e.target.closest("tr").dataset.i, 1);
    drawGlossary();
  };
  drawExtra();
  findGloss();
}
// Tìm trong bảng: chỉ ẩn dòng (không vẽ lại), để data-i vẫn trỏ đúng vào GLOSS; không phân biệt hoa thường và dấu
const fold = s => String(s ?? "").normalize("NFD").replace(/\p{M}/gu, "").replace(/[đĐ]/g, "d").toLowerCase();
function findGloss() {
  const q = fold($("gFind").value.trim());
  let hit = 0;
  $("gtab").querySelectorAll("tr[data-i]").forEach(tr => {
    const t = GLOSS[+tr.dataset.i], ok = !q || fold(t.term).includes(q) || fold(t.vi).includes(q);
    tr.style.display = ok ? "" : "none";
    hit += ok;
  });
  $("gextra").querySelectorAll(".gchip").forEach(b => {
    b.style.display = !q || fold(EXTRA[+b.dataset.i]?.term).includes(q) ? "" : "none";
  });
  $("gFound").textContent = q ? `khớp ${hit}/${GLOSS.length}` : "";
}
$("gFind").addEventListener("input", findGloss);
$("gFind").addEventListener("keydown", e => { if (e.key === "Escape") { $("gFind").value = ""; findGloss(); } });
try { $("ghelp").open = localStorage.getItem("ghelp_open") === "1"; } catch {}
$("ghelp").addEventListener("toggle", () => { try { localStorage.setItem("ghelp_open", $("ghelp").open ? "1" : "0"); } catch {} });
// Gợi ý khác: bấm để đưa vào bảng (mặc định giữ nguyên tiếng Anh, đổi sang "dịch" rồi gõ cách dịch nếu cần)
function drawExtra() {
  const have = new Set(GLOSS.map(t => t.term.trim().toLowerCase()));
  const chip = (x, i) => `<button type="button" class="gchip${x.hidden ? " hid" : ""}" data-i="${i}"
      title="${x.hidden ? "Bạn từng xoá thuật ngữ này — bấm để bỏ ẩn và thêm lại" : "Thêm vào bảng"}">${x.hidden ? "↺" : "+"} ${esc(x.term)}<small>${x.n}</small></button>`;
  const list = EXTRA.map((x, i) => [x, i]).filter(([x]) => !have.has(x.term.trim().toLowerCase()));
  const sug = list.filter(([x]) => !x.hidden), hid = list.filter(([x]) => x.hidden);
  $("gextra").innerHTML =
    (sug.length ? `<div><span class="hint">Gợi ý khác — LLM cho là không phải thuật ngữ, bấm để thêm:</span> ${sug.map(([x, i]) => chip(x, i)).join("")}</div>` : "") +
    (hid.length ? `<div><span class="hint">Đang ẩn vì bạn từng xoá:</span> ${hid.map(([x, i]) => chip(x, i)).join("")}</div>` : "");
  $("gextra").onclick = async e => {
    const b = e.target.closest(".gchip");
    if (!b) return;
    const x = EXTRA[+b.dataset.i];
    if (x.hidden) await fetch(`/api/user_terms/blocked?term=${encodeURIComponent(x.term)}`, { method:"DELETE" });
    GLOSS.push({ term: x.term, vi: "", keep: true, use: true });
    EXTRA.splice(+b.dataset.i, 1);
    drawGlossary();
    $("gwrap").scrollTop = $("gwrap").scrollHeight;
  };
  $("gtab").oninput = e => {
    const tr = e.target.closest("tr"), t = GLOSS[+tr.dataset.i];
    if (e.target.classList.contains("guse")) { t.use = e.target.checked; tr.classList.toggle("off", !t.use); }
    if (e.target.classList.contains("gmode")) {
      t.keep = e.target.value === "keep";
      tr.querySelector(".gvi").disabled = t.keep;
    }
    if (e.target.classList.contains("gvi")) t.vi = e.target.value;
    if (e.target.classList.contains("gterm")) t.term = e.target.value;
    countGloss();
  };
}
const countGloss = () => $("gcount").textContent =
  `${GLOSS.filter(t => t.use).length}/${GLOSS.length} thuật ngữ được dùng · ${GLOSS.filter(t => t.use && t.keep).length} giữ nguyên tiếng Anh`;

$("gSave").onclick = async () => {
  GLOSS = GLOSS.filter(t => t.term.trim());
  const r = await fetch(`/api/${doc.id}/glossary`, { method:"PUT", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ terms: GLOSS }) });
  const j = await r.json();
  EXTRA = j.extra || EXTRA;
  drawGlossary();
  setStatus(`Đã lưu bảng thuật ngữ (${j.used} thuật ngữ) – trang đã dịch cần bấm “↻ Dịch lại” để áp bảng mới`);
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

// ================================================================ Trò chuyện về tài liệu
let chatMsgs = [], kept = null, streaming = false;
// mở khung bên phải ở tab trò chuyện (tab, và nút 💬 / 📝 trên thanh trên cùng: xem notes.js)
const openChat = () => (window.showTab ? showTab("chat") : document.body.classList.add("chat"));
$("chatClose").onclick = () => document.body.classList.remove("chat");
$("chatClear").onclick = () => { chatMsgs = []; $("msgs").innerHTML = ""; dropSel(); };
$("selDrop").onclick = () => dropSel();
function keepSel(info) {
  kept = info && { text: info.text, page: info.page, side: info.side, blocks: info.blocks };
  $("selbox").hidden = !kept;
  if (kept) $("seltext").textContent = `Trang ${kept.page + 1} · ${kept.side === "translated" ? "bản dịch" : "bản gốc"}: “${kept.text.slice(0, 220)}”`;
}
const dropSel = () => keepSel(null);

function bubble(cls, text, quote) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  if (quote) { const q = document.createElement("span"); q.className = "quote"; q.textContent = quote; el.appendChild(q); }
  el.appendChild(document.createTextNode(text));
  $("msgs").appendChild(el);
  $("msgs").scrollTop = $("msgs").scrollHeight;
  return el;
}
/** Bong bóng trả lời của LLM: chữ được dựng thành HTML (Markdown, bảng, công thức) trong `.body`;
 *  bản Markdown thô giữ ở `el._src` để nút "Chép" chép được đúng LaTeX/bảng chứ không phải chữ đã dựng. */
function aiBubble() {
  const el = bubble("ai", "");
  el.textContent = "";
  el._src = "";
  el.body = document.createElement("div");
  el.body.className = "body";
  el.body.textContent = "…";
  el.appendChild(el.body);
  return el;
}
function finishAi(el) {
  const bar = document.createElement("div");
  bar.className = "msgbar";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "📋 Chép";
  copy.title = "Chép câu trả lời dạng Markdown (giữ nguyên công thức LaTeX và bảng)";
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(el._src); copy.textContent = "✓ Đã chép"; }
    catch { copy.textContent = "✗ Không chép được"; }
    setTimeout(() => { copy.textContent = "📋 Chép"; }, 1500);
  };
  bar.appendChild(copy);
  window.noteSaveButton?.(el, bar);                    // 📌 Lưu thành ghi chú (notes.js)
  el.appendChild(bar);
}
$("q").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask").requestSubmit(); }
});
$("ask").onsubmit = async e => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q || !doc || streaming) return;
  streaming = true;
  $("q").value = "";
  const used = kept;                                   // đoạn bôi đen chỉ gửi kèm đúng câu hỏi này
  const extra = window.noteChatExtra?.() || { note_ids: [], page: null, quote: "" };   // ghi chú "💬 Hỏi lại"
  bubble("me", q, used ? used.text.slice(0, 300) : extra.quote);
  chatMsgs.push({ role:"user", content:q });
  const out = aiBubble();
  out._q = q; out._used = used; out._notePage = extra.page;   // cho nút 📌 Lưu thành ghi chú
  let acc = "";
  try {
    const r = await fetch(`/api/${doc.id}/chat`, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ cfg: cfg(), messages: chatMsgs, page: used ? used.page : (extra.page ?? visiblePage()),
                             selection: used ? used.text : "", side: used ? used.side : "",
                             note_ids: extra.note_ids }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      acc += dec.decode(value, { stream:true });
      out._src = acc;
      paintInto(out.body, acc);                        // tiết lưu ~80ms; công thức đã dựng được nhớ lại, không dựng lại
      $("msgs").scrollTop = $("msgs").scrollHeight;
    }
    paintInto(out.body, acc, true);                    // lần cuối: vẽ đủ, kể cả công thức
    finishAi(out);
    $("msgs").scrollTop = $("msgs").scrollHeight;
    chatMsgs.push({ role:"assistant", content:acc });
  } catch (err) {
    out.className = "msg err";
    out.textContent = "❌ " + err.message;
    chatMsgs.pop();
  }
  streaming = false;
  dropSel();
  window.noteAfterSend?.();
};
// Trang đang xem (để LLM lấy đúng phần tài liệu làm ngữ cảnh khi không bôi đen gì)
function visiblePage() {
  let best = null, top = Infinity;
  document.querySelectorAll('.page').forEach(el => {             // cả hai bản: chế độ xem một cột ẩn một bản
    if (!el.offsetParent) return;
    const r = el.getBoundingClientRect();
    if (r.bottom > 80 && Math.abs(r.top) < top) { top = Math.abs(r.top); best = +el.dataset.p; }
  });
  return best;
}

// ================================================================ Khởi động
async function loadFonts() {
  try {
    const { fonts } = await (await fetch("/api/fonts")).json();
    document.querySelectorAll(".font-sel").forEach(sel => {
      sel.innerHTML = `<option value="auto">Tự động (font tốt nhất đang có)</option>` +
        fonts.filter(f => f.kind === sel.dataset.kind).map(f =>
          `<option value="${f.name}" ${f.available ? "" : "disabled"}>${f.name}${f.available ? "" : " (chưa tải)"}</option>`).join("");
    });
  } catch {}
}
loadFonts().then(loadKeys).then(loadProviders).then(loadSettings).then(() => restoreDoc());
