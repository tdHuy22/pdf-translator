// Kiểm listener `message` của static/graph.js (TM-18) bằng node, không cần trình duyệt: chạy nguyên file trong một
// ngữ cảnh `vm` có DOM giả tối thiểu (các biến app.js, view.js mà graph.js dùng: doc, $, cfg, pageOf, loadLayer,
// viewScrollTo), bắt hàm nghe `message` rồi gửi tin giả và xem trang có cuộn tới đâu.
//
//     node scripts/test_graph_js.mjs        # scripts/test_graph.py cũng gọi lệnh này nếu máy có node
//
// In ✓/✗ cho từng mục, mã thoát 1 nếu có mục trượt.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../static/graph.js", import.meta.url), "utf8");
const FAIL = [];
const ok = (cond, msg) => { console.log((cond ? "  ✓ " : "  ✗ ") + msg); if (!cond) FAIL.push(msg); };

const frameWin = { name: "khung sơ đồ" }, otherWin = { name: "trang khác" };
let calls, removed;
const el = (extra = {}) => ({
  hidden: false, textContent: "", dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  getBoundingClientRect: () => ({ width: 300 }), removeAttribute() {}, querySelectorAll: () => [], ...extra,
});
const els = { graphFrame: el({ contentWindow: frameWin }), graphPane: el({ hidden: true }), chat: el() };
const page = (p, kind) => el({
  offsetParent: {}, clientWidth: 100,
  querySelector(sel) {
    calls.push(["query", p, kind, sel]);
    if (sel === ".layer") return { dataset: { w: "100" } };
    const m = /^\.blk\[data-b="(\d+)"\]$/.exec(sel);
    return m ? el({ style: { top: "40" } }) : null;
  },
});
let listener = null;
const ctx = {
  doc: { id: "abcdefabcdef", pages: 5, done: new Set([0, 1, 2, 3, 4]) },
  $: id => els[id] || el(), cfg: () => ({}), console,
  pageOf: (p, kind) => { calls.push(["page", p, kind]); return page(p, kind); },
  loadLayer: async () => {}, viewScrollTo: (e, y) => calls.push(["scroll", y]),
  document: { body: { classList: { contains: () => false, remove: c => removed.push(c) } }, querySelectorAll: () => [] },
  localStorage: { getItem: () => null, setItem() {} },
  addEventListener: (type, fn) => { if (type === "message") listener = fn; },
  setTimeout: () => 0, clearTimeout() {}, innerWidth: 1200, fetch: async () => ({ ok: false }),
};
ctx.window = ctx;
vm.runInNewContext(src, ctx, { filename: "static/graph.js" });
ok(typeof listener === "function", "graph.js đăng ký hàm nghe `message`");

async function send(data, source = frameWin) {
  calls = []; removed = [];
  listener({ source, data });
  await new Promise(r => setImmediate(r));   // reveal là hàm async
  return { pages: calls.filter(c => c[0] === "page").map(c => c[1]), blocks: calls.filter(c => c[0] === "query" && c[3] !== ".layer").map(c => c[3]),
           scrolls: calls.filter(c => c[0] === "scroll").map(c => c[1]) };
}
const silent = r => r.pages.length === 0 && r.blocks.length === 0 && r.scrolls.length === 0;

let r = await send({ pg: "anchor", page: 2, block: "p2-b3" });
ok(r.pages.join() === "1,1" && r.blocks.join() === '.blk[data-b="2"],.blk[data-b="2"]' && r.scrolls.join() === "40,40",
   "tin đúng từ khung sơ đồ: cuộn hai bản tới trang 2, đoạn p2-b3 (data-b=2)");
r = await send({ pg: "anchor", page: 1, block: "p1-b1" });
const r5 = await send({ pg: "anchor", page: 5, block: "p5-b2" });
ok(r.pages.join() === "0,0" && r.blocks.join() === '.blk[data-b="0"],.blk[data-b="0"]' && r5.pages.join() === "4,4"
   && r5.blocks.join() === '.blk[data-b="1"],.blk[data-b="1"]', "biên nhận: trang 1 và trang cuối (5/5) vẫn cuộn tới đoạn");
ok(silent(await send({ pg: "anchor", page: 2, block: "p2-b3" }, otherWin)), "tin từ cửa sổ khác (iframe, tab khác) bị bỏ qua");
ok(silent(await send({ pg: "anchor", page: 2, block: "p2-b3" }, null)), "tin không có nguồn bị bỏ qua");
const saved = ctx.doc;
ctx.doc = null;
ok(silent(await send({ pg: "anchor", page: 2, block: "p2-b3" })), "chưa mở bài nào: bỏ qua, không lỗi");
ctx.doc = saved;
const leaked = [];
for (const bad of ["2", 2.5, 0, -1, 6, NaN, Infinity, 1e9, null, [2], { valueOf: () => 2 }])
  if (!silent(await send({ pg: "anchor", page: bad, block: "p2-b1" }))) leaked.push(String(bad));
ok(!leaked.length, (leaked.length ? `lọt qua: ${leaked.join(", ")}; ` : "") + "trang không phải số nguyên trong 1..số trang (\"2\", 2.5, 0, -1, 6, NaN, ∞, 1e9, null, [2], đối tượng) bị bỏ qua");
ok(silent(await send({ pg: "other", page: 2, block: "p2-b1" })) && silent(await send(null)) && silent(await send("anchor"))
   && silent(await send({ page: 2 })), "tin không phải {pg: \"anchor\"} bị bỏ qua");

for (const [block, why] of [["p3-b1", "đoạn của trang khác"], ['p2-b1"],.x[data-b="1', "chèn bộ chọn CSS"], ["p2-b12345", "số quá 4 chữ số"],
                            [" p2-b1", "có khoảng trắng"], ["p2-b1\n", "xuống dòng"], [{ toString: () => "p2-b1" }, "đối tượng giả chuỗi"], [undefined, "thiếu"]]) {
  r = await send({ pg: "anchor", page: 2, block });
  ok(r.pages.join() === "1,1" && r.blocks.length === 0 && r.scrolls.join() === "0,0", `block ${why}: chỉ cuộn tới đầu trang 2, không tra đoạn`);
}
r = await send({ pg: "anchor", page: 2, block: "p2-b0" });
ok(r.blocks.join() === '.blk[data-b="-1"],.blk[data-b="-1"]' && r.scrolls.join() === "0,0", "p2-b0: tra data-b=-1, không có đoạn đó nên cuộn đầu trang");

// ST (trạng thái sơ đồ) là biến trong hàm của graph.js: đặt qua graphShown() → refresh() như khi mở tab Sơ đồ
ctx.fetch = async () => ({ ok: true, text: async () => "", json: async () => ({ available: true, graph: true, draft: false, stale: [2], job: null, pages: 5 }) });
els.graphPane.hidden = false;
ctx.document.body.classList.contains = c => c === "chat";
ctx.graphShown();
await new Promise(r => setTimeout(r, 10));
r = await send({ pg: "anchor", page: 2, block: "p2-b3" });
ok(r.pages.join() === "1,1" && r.blocks.length === 0, "trang đã trích lại sau khi dựng (stale): không tra đoạn theo id cũ");
r = await send({ pg: "anchor", page: 3, block: "p3-b1" });
ok(r.blocks.join() === '.blk[data-b="0"],.blk[data-b="0"]', "trang khác vẫn tra đoạn bình thường");

ctx.doc.done = new Set([0]);
r = await send({ pg: "anchor", page: 3, block: "p3-b1" });
ok(r.pages.join() === "2,2" && r.scrolls.length === 1, "trang chưa dịch: chỉ cuộn bản gốc");
ctx.doc.done = new Set([0, 1, 2, 3, 4]);

els.chat.getBoundingClientRect = () => ({ width: 1190 });   // khung sơ đồ che kín bài (điện thoại)
r = await send({ pg: "anchor", page: 2, block: "p2-b1", go: true });
ok(removed.join() === "chat", "\"Xem trong bài\" (go: true) khi khung che kín bài: đóng khung");
await send({ pg: "anchor", page: 2, block: "p2-b1", go: "true" });
ok(removed.length === 0, "go phải đúng true (chuỗi \"true\" không đóng khung)");
await send({ pg: "anchor", page: 2, block: "p2-b1" });
ok(removed.length === 0, "bấm ô (không go) không đóng khung");
els.chat.getBoundingClientRect = () => ({ width: 300 });
await send({ pg: "anchor", page: 2, block: "p2-b1", go: true });
ok(removed.length === 0, "màn rộng (khung không che bài): go không đóng khung");

console.log("\n" + (FAIL.length ? `${FAIL.length} MỤC TRƯỢT` : "TẤT CẢ ĐẠT"));
process.exit(FAIL.length ? 1 : 0);
