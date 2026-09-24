"""Dựng `bench/quality/report.html` để NGƯỜI chấm mù các bản dịch của `bench_quality.py`.

    .venv/bin/python scripts/quality_report.py && open bench/quality/report.html

Vì sao vẫn cần người chấm khi đã có `judge_quality.py`: trọng tài cũng chỉ là một model, và ở đây nó chạy trên
GPU 4 GB nên không mạnh. Điểm người chấm (dù chỉ vài chục đoạn) là mốc để biết có tin trọng tài được không —
hai bên xếp hạng khác nhau thì tin người và bỏ điểm trọng tài đi.

Trang web: cùng bộ đoạn mà trọng tài chấm, mỗi đoạn hiện bản gốc và các bản dịch đánh nhãn A/B/C/D theo hoán vị
riêng (không biết bản nào của model nào). Chấm 1–5 cho từng bản, chọn bản tốt nhất/kém nhất. Bài làm tự lưu vào
localStorage của trình duyệt, bấm "Hiện tên hệ thống" để lật bài và xem bảng tổng (kèm điểm trọng tài để so).
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _quality import AXES, OUT, load_runs, pick  # noqa: E402

PAGE = """<!doctype html><html lang="vi"><meta charset="utf-8">
<title>Chấm mù chất lượng dịch</title>
<style>
 :root{--line:#dcdcdc;--bg:#fff;--fg:#1a1a1a;--muted:#666;--src:#f6f7f9;--hl:#fff9e0}
 @media (prefers-color-scheme:dark){:root{--line:#3a3a3a;--bg:#1c1c1e;--fg:#ececec;--muted:#9a9a9a;--src:#26262a;--hl:#3a3520}}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg);
      margin:0 auto;padding:16px;max-width:900px}
 h1{font-size:19px} .hint{color:var(--muted);font-size:13px}
 .bar{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;z-index:9;
      display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 button{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);
        color:var(--fg);cursor:pointer}
 .item{border:1px solid var(--line);border-radius:10px;padding:12px;margin:14px 0}
 .src{background:var(--src);border-radius:8px;padding:10px;white-space:pre-wrap}
 .cand{border-top:1px solid var(--line);padding:10px 0;display:grid;grid-template-columns:28px 1fr;gap:10px}
 .lab{font-weight:700} .who{color:var(--muted);font-size:12px}
 .vi{white-space:pre-wrap}
 .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:6px;font-size:13px;color:var(--muted)}
 select{font:inherit;padding:2px 4px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:5px}
 table{border-collapse:collapse;margin-top:10px;width:100%%} td,th{border:1px solid var(--line);padding:5px 8px;text-align:right}
 th:first-child,td:first-child{text-align:left}
 mark{background:var(--hl);color:inherit}
</style>
<h1>Chấm mù chất lượng dịch – %(n)d đoạn</h1>
<p class="hint">Mỗi đoạn: bản gốc rồi các bản dịch A/B/C/D (thứ tự xáo riêng từng đoạn, chưa biết của model nào).
Chấm 1–5 theo <b>nghĩa</b> (đúng ý bản gốc), <b>thuật ngữ</b>, <b>tự nhiên</b>; rồi chọn bản tốt nhất và kém nhất.
Bài tự lưu trong trình duyệt. Chấm xong bấm <b>Hiện tên hệ thống</b>.</p>
<div class="bar">
  <button id="reveal">Hiện tên hệ thống</button>
  <button id="export">Xuất JSON</button>
  <button id="reset">Xoá bài đã chấm</button>
  <span class="hint" id="prog"></span>
</div>
<div id="list"></div>
<div id="sum"></div>
<script>
const DATA = %(data)s, JUDGE = %(judge)s, AXES = %(axes)s;
const KEY = "vietpaper-quality-human";
let ans = {}; try { ans = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(ans)); } catch {}; progress(); };
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
let shown = false;

function progress() {
  const done = DATA.filter(it => (ans[it.id] || {}).best).length;
  document.getElementById("prog").textContent = `đã chấm ${done}/${DATA.length}`;
}

function draw() {
  document.getElementById("list").innerHTML = DATA.map(it => {
    const a = ans[it.id] || {};
    const cands = it.labels.map(([lab, sys, vi]) => `
      <div class="cand">
        <div class="lab">${lab}<div class="who" data-who="${esc(sys)}"></div></div>
        <div>
          <div class="vi">${esc(vi)}</div>
          <div class="row">
            ${AXES.map(([k, name]) => `<label>${name}
              <select data-it="${it.id}" data-lab="${lab}" data-ax="${k}">
                ${[0,1,2,3,4,5].map(v => `<option value="${v}"${(a[lab]||{})[k]===v?" selected":""}>${v||"–"}</option>`).join("")}
              </select></label>`).join("")}
            <label><input type="radio" name="best-${it.id}" value="${lab}"${a.best===lab?" checked":""}> tốt nhất</label>
            <label><input type="radio" name="worst-${it.id}" value="${lab}"${a.worst===lab?" checked":""}> kém nhất</label>
          </div>
        </div>
      </div>`).join("");
    return `<div class="item"><div class="hint">${esc(it.paper)} · trang ${it.page} · đoạn ${it.block}</div>
      <div class="src">${esc(it.src)}</div>${cands}</div>`;
  }).join("");
  document.querySelectorAll("select").forEach(s => s.onchange = e => {
    const {it, lab, ax} = e.target.dataset;
    ((ans[it] ||= {})[lab] ||= {})[ax] = +e.target.value;
    save();
  });
  document.querySelectorAll('input[type=radio]').forEach(r => r.onchange = e => {
    const [kind, id] = e.target.name.split("-");
    (ans[id] ||= {})[kind] = e.target.value;
    save();
  });
  progress();
}

document.getElementById("reveal").onclick = () => {
  shown = !shown;
  document.querySelectorAll("[data-who]").forEach(el => el.textContent = shown ? el.dataset.who : "");
  document.getElementById("reveal").textContent = shown ? "Ẩn tên hệ thống" : "Hiện tên hệ thống";
  document.getElementById("sum").innerHTML = shown ? summary() : "";
};
document.getElementById("export").onclick = () => {
  const blob = new Blob([JSON.stringify({answers: ans, data: DATA}, null, 1)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "human.json"; a.click();
};
document.getElementById("reset").onclick = () => {
  if (confirm("Xoá toàn bộ bài đã chấm?")) { ans = {}; save(); draw(); }
};

function summary() {
  const sys = {};
  const add = (s, k, v) => { (sys[s] ||= {n:0, best:0, worst:0, ...Object.fromEntries(AXES.map(([a])=>[a,0]))})[k] += v; };
  for (const it of DATA) {
    const a = ans[it.id] || {};
    for (const [lab, s] of it.labels) {
      const sc = a[lab] || {};
      if (AXES.some(([k]) => sc[k])) { add(s, "n", 1); AXES.forEach(([k]) => add(s, k, sc[k] || 0)); }
      if (a.best === lab) add(s, "best", 1);
      if (a.worst === lab) add(s, "worst", 1);
    }
  }
  const jm = {};   // điểm trung bình của trọng tài LLM, để so với điểm người
  for (const r of (JUDGE.rows || [])) for (const [s, sc] of Object.entries(r.scores || {})) {
    (jm[s] ||= {n:0, t:0}); jm[s].n++; jm[s].t += AXES.reduce((x,[k]) => x + (sc[k]||0), 0) / AXES.length;
  }
  const rows = Object.entries(sys).map(([s, v]) => {
    const avg = AXES.map(([k]) => v.n ? v[k]/v.n : 0);
    return `<tr><td>${esc(s)}</td>${avg.map(x=>`<td>${x.toFixed(2)}</td>`).join("")}
      <td><b>${(avg.reduce((a,b)=>a+b,0)/AXES.length).toFixed(2)}</b></td>
      <td>${v.best}</td><td>${v.worst}</td>
      <td>${jm[s] ? (jm[s].t/jm[s].n).toFixed(2) : "–"}</td></tr>`;
  }).join("");
  return `<h2>Tổng kết</h2><table><tr><th>hệ thống</th>${AXES.map(([,n])=>`<th>${n}</th>`).join("")}
    <th>trung bình</th><th>nhất</th><th>bét</th><th>trọng tài ${esc(JUDGE.judge || "–")}</th></tr>${rows}</table>
    <p class="hint">Cột cuối là điểm của trọng tài LLM trên cùng bộ đoạn. Thứ hạng hai bên lệch nhau thì
    tin cột "trung bình" (người chấm) và bỏ điểm trọng tài.</p>`;
}
draw();
</script></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="google,2b,4b,9b")
    ap.add_argument("--per-page", type=int, default=6)
    ap.add_argument("--min-chars", type=int, default=120)
    ap.add_argument("--seed", type=int, default=11, help="hoán vị nhãn A/B/C/D (khác seed của trọng tài)")
    args = ap.parse_args()

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    items = pick(load_runs(), systems, args.per_page, args.min_chars)
    if not items:
        sys.exit("Không có đoạn nào mọi hệ thống đều dịch — chạy bench_quality.py trước")
    rng = random.Random(args.seed)
    data = []
    for it in items:
        order = list(it["cand"])
        rng.shuffle(order)                                   # hoán vị riêng từng đoạn: người chấm cũng thiên vị vị trí
        data.append({"id": "-".join(map(str, it["key"])), "paper": it["key"][0], "page": it["key"][1],
                     "block": it["key"][2], "src": it["src"],
                     "labels": [[chr(65 + k), s, it["cand"][s]] for k, s in enumerate(order)]})
    judge = json.loads((OUT / "judge.json").read_text()) if (OUT / "judge.json").exists() else {}
    axes = [[a, n] for a, n in zip(AXES, ("nghĩa", "thuật ngữ", "tự nhiên"))]
    path = OUT / "report.html"
    path.write_text(PAGE % {"n": len(data), "data": json.dumps(data, ensure_ascii=False),
                            "judge": json.dumps(judge, ensure_ascii=False), "axes": json.dumps(axes, ensure_ascii=False)})
    print(f"{path} – {len(data)} đoạn, {len(systems)} hệ thống"
          + (f", kèm điểm trọng tài {judge.get('judge')}" if judge else " (chưa có điểm trọng tài)"))
    print(f"  open {path}")


main()
