"""Chấm NGHĨA cho kết quả của `bench_quality.py`: một model trọng tài đọc chữ gốc và các bản dịch, chấm mù.

    .venv/bin/python scripts/judge_quality.py                       # trọng tài mặc định: gpt-oss trên TUF
    .venv/bin/python scripts/judge_quality.py --model <model> --base-url <url> --api-key <key>

Vì sao cần: các chỉ số của `bench_quality.py` (giữ `{vN}`, không tràn khung, đúng bảng thuật ngữ, tốc độ) đo
KỶ LUẬT dịch, không đo NGHĨA — một model dịch sai hẳn vẫn đạt 100% cả bốn. Bước này mới xếp được hạng.

Cách chấm để kết quả dùng được:
  - **Trọng tài không nằm trong danh sách ứng viên.** Mặc định `gpt-oss` (20B, của OpenAI) trên TUF: khác họ với
    Qwen 2b/4b/9b nên không thiên vị "văn của nhà mình". Có key OpenRouter thì thay bằng model mạnh hơn.
  - **Chấm mù, xáo thứ tự từng mục**: mỗi đoạn các bản dịch được đánh nhãn A/B/C/D theo một hoán vị riêng.
    Trọng tài LLM thiên vị vị trí rất mạnh, không xáo là đo nhầm vị trí chứ không phải chất lượng.
  - **Chỉ chấm đoạn mà MỌI hệ thống đều có bản dịch**: model nào bỏ qua đoạn khó mà vẫn được chấm phần dễ thì
    tự nhiên điểm cao. Phần bỏ sót đã nằm ở cột "sót" của `bench_quality.py` rồi.
  - Mặc định chỉ chấm đoạn thân bài đủ dài (`--min-chars`), tối đa `--per-page` đoạn mỗi trang: ô bảng một hai
    chữ thì chấm cũng không phân biệt được model nào hơn.

Kết quả: `bench/quality/judge.json` (điểm từng đoạn, kèm hoán vị nhãn để kiểm lại) + bảng tổng in ra màn hình.
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
from _quality import AXES, OUT, load_runs, pick  # noqa: E402

JUDGE_PROMPT = """Bạn là giám khảo dịch thuật học thuật Anh→Việt. Bạn nhận một đoạn trong bài báo khoa học và
vài bản dịch tiếng Việt được đánh nhãn A, B, C, D. Chấm từng bản theo 3 tiêu chí, thang 1–5 (5 là tốt nhất):
- adequacy: đúng nghĩa bản gốc, không thêm, không bớt, không đảo ngược ý, số liệu và trích dẫn đúng.
- terminology: thuật ngữ chuyên ngành dịch đúng hoặc giữ nguyên tiếng Anh đúng thói quen học thuật.
- fluency: tiếng Việt tự nhiên, đúng ngữ pháp, không lẫn chữ Trung, không dịch máy móc.
Quy ước: {v1}, {v2}… là chỗ giữ cho công thức, coi như đúng nếu còn nguyên và đứng đúng chỗ.
Chấm độc lập từng bản, đừng vì thứ tự A/B/C/D mà ưu tiên.
CHỈ trả JSON, không giải thích thêm:
{"scores":[{"label":"A","adequacy":4,"terminology":3,"fluency":4,"note":"lý do ngắn"}],"best":"A"}"""


async def judge_one(cfg: app.Config, item: dict, rng: random.Random, args) -> dict:
    labels = list("ABCD")
    order = list(item["cand"])
    rng.shuffle(order)                                        # hoán vị riêng cho từng đoạn
    mapping = dict(zip(labels, order))
    body_txt = "\n\n".join(f"[{l}] {item['cand'][s]}" for l, s in mapping.items())
    user = f"[Bản gốc]\n{item['src']}\n\n[Các bản dịch]\n{body_txt}"
    body = {"model": cfg.model, "temperature": 0.0, "max_tokens": args.max_tokens,
            "messages": [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}]}
    if args.no_temperature:
        body.pop("temperature")
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    content = ""
    for attempt in range(2):                                  # trọng tài nghĩ quá dài → nới trần rồi hỏi lại
        body["max_tokens"] = args.max_tokens * (attempt + 2)
        try:
            async with app.llm_client(timeout=900) as c:   # cả trọng tài Claude Code (claude_code.py)
                r = await c.post(cfg.base_url.rstrip("/") + "/chat/completions", headers=headers, json=body)
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            return {**{k: item[k] for k in ("key", "src")}, "mapping": mapping, "error": repr(e)[:200]}
        if content:
            break
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    data, rows = parse_scores(content)
    if data is None and not rows:
        return {"key": item["key"], "mapping": mapping, "error": "trọng tài không trả JSON đọc được"}
    scores = {}
    for row in rows:
        if (s := mapping.get(str(row.get("label", "")).strip().upper()[:1])):
            scores[s] = {a: max(1, min(5, int(row.get(a, 0) or 0))) for a in AXES} | {"note": str(row.get("note", ""))[:200]}
    return {"key": item["key"], "mapping": mapping, "scores": scores,
            "best": mapping.get(str((data or {}).get("best", "")).strip().upper()[:1], "")}


_ROW = re.compile(r'\{[^{}]*?"label"\s*:\s*"([A-D])"[^{}]*?\}', re.S)
_NUM = re.compile(r'"(adequacy|terminology|fluency)"\s*:\s*(\d)')


def parse_scores(content: str) -> tuple[dict | None, list[dict]]:
    """JSON của trọng tài. Model chạy trên GPU 4 GB hay nói thêm hoặc bị cắt ngang, nên hỏng thì vớt lại từng mục
    điểm còn nguyên vẹn (cùng cách `app.parse_json` vớt bản dịch) — hơn là bỏ cả đoạn không chấm."""
    if (m := re.search(r"\{.*\}", content, re.S)):
        try:
            data = json.loads(m.group(0))
            if isinstance(data.get("scores"), list):
                return data, data["scores"]
        except ValueError:
            pass
    rows = []
    for m in _ROW.finditer(content):
        rows.append({"label": m.group(1), **{k: int(v) for k, v in _NUM.findall(m.group(0))}})
    best = re.search(r'"best"\s*:\s*"([A-D])"', content)
    return ({"best": best.group(1)} if best else None), rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="google,2b,4b,9b")
    ap.add_argument("--base-url", default=os.environ.get("VP_TUF_URL", "http://localhost:8080/v1"))
    ap.add_argument("--model", default="gpt-oss", help="model TRỌNG TÀI, không được nằm trong --systems")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--per-page", type=int, default=6)
    ap.add_argument("--min-chars", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="judge.json", help="tên file kết quả trong bench/quality (một file mỗi trọng tài)")
    ap.add_argument("--no-temperature", action="store_true", help="trọng tài không nhận temperature (gpt-6-luna qua proxy ChatGPT)")
    args = ap.parse_args()

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    if args.model in systems:
        sys.exit(f"Trọng tài '{args.model}' đang nằm trong danh sách ứng viên — chọn model khác")
    cfg = app.Config(base_url=args.base_url, api_key=args.api_key or app.remember(args.base_url, ""), model=args.model,
                     temperature=0.0)
    items = pick(load_runs(), systems, args.per_page, args.min_chars)
    if not items:
        sys.exit("Không có đoạn nào mọi hệ thống đều dịch — chạy bench_quality.py trước")
    print(f"Trọng tài: {args.model} · {len(items)} đoạn (đoạn nào cũng có đủ {len(systems)} bản dịch)")

    rng = random.Random(args.seed)
    rows = []
    for k, it in enumerate(items, 1):
        r = await judge_one(cfg, it, rng, args)
        rows.append(r)
        mark = "⚠️ " + r["error"] if r.get("error") else " ".join(
            f"{s}:{sum(r['scores'][s][a] for a in AXES) / 3:.1f}" for s in systems if s in r.get("scores", {}))
        print(f"  [{k:>3}/{len(items)}] {r['key'][0]} tr.{r['key'][1]} đoạn {r['key'][2]:>3}  {mark}", flush=True)
    (OUT / args.out).write_text(json.dumps(
        {"judge": args.model, "seed": args.seed, "rows": [{**r, "key": list(r["key"])} for r in rows]},
        ensure_ascii=False, indent=1))

    print(f"\n=== Điểm trung bình (trọng tài {args.model}, {sum(not r.get('error') for r in rows)} đoạn) ===")
    print(f"{'hệ thống':10} {'nghĩa':>7} {'thuật ngữ':>10} {'tự nhiên':>9} {'trung bình':>11} {'số lần nhất':>12}")
    for s in systems:
        got = [r["scores"][s] for r in rows if s in r.get("scores", {})]
        if not got:
            print(f"{s:10} {'—':>7}")
            continue
        avg = {a: sum(g[a] for g in got) / len(got) for a in AXES}
        print(f"{s:10} {avg['adequacy']:7.2f} {avg['terminology']:10.2f} {avg['fluency']:9.2f} "
              f"{sum(avg.values()) / 3:11.2f} {sum(r.get('best') == s for r in rows):12}")
    diagnose(rows, systems)


def diagnose(rows: list[dict], systems: list[str]) -> None:
    """Trọng tài có đủ tinh để phân biệt các hệ thống không. Thước đo chính: **thiên vị vị trí** — nhãn A/B/C/D
    đã được xáo ngẫu nhiên nên điểm trung bình của bốn nhãn lẽ ra phải bằng nhau; chênh bao nhiêu chính là
    nhiễu của trọng tài. Chênh lệch giữa hai hệ thống mà NHỎ HƠN mức nhiễu đó thì không kết luận được gì."""
    mean = lambda xs: sum(xs) / len(xs) if xs else 0                                        # noqa: E731
    score = lambda sc: sum(sc[a] for a in AXES) / len(AXES)                                 # noqa: E731
    by_label: dict[str, list[float]] = {}
    for r in rows:
        for lab, name in (r.get("mapping") or {}).items():
            if name in (r.get("scores") or {}):
                by_label.setdefault(lab, []).append(score(r["scores"][name]))
    if not by_label:
        return
    label_avg = {lab: mean(v) for lab, v in sorted(by_label.items())}
    noise = max(label_avg.values()) - min(label_avg.values())
    sys_avg = {s: mean([score(r["scores"][s]) for r in rows if s in r.get("scores", {})]) for s in systems}
    sys_avg = {s: v for s, v in sys_avg.items() if v}
    ranked = sorted(sys_avg.items(), key=lambda kv: -kv[1])

    print("\n=== Trọng tài có đáng tin không ===")
    print("  thiên vị vị trí: " + " · ".join(f"{lab} {v:.2f}" for lab, v in label_avg.items())
          + f"  → nhiễu {noise:.2f} điểm (nhãn đã xáo nên lẽ ra phải bằng nhau)")
    blurred = [f"{a}≈{b}" for (a, x), (b, y) in zip(ranked, ranked[1:]) if abs(x - y) < noise]
    if blurred:
        print(f"  ⚠️  cách nhau ÍT HƠN mức nhiễu, KHÔNG xếp hạng được: {', '.join(blurred)}")
    if (clear := [f"{a}>{b}" for (a, x), (b, y) in zip(ranked, ranked[1:]) if abs(x - y) >= noise]):
        print(f"  tách được rõ: {', '.join(clear)}")
    ties = sum(1 for r in rows if (s := r.get("scores")) and
               sum(1 for v in s.values() if score(v) == max(score(w) for w in s.values())) > 1)
    print(f'  "số lần nhất": {ties}/{len(rows)} đoạn có nhiều bản đồng hạng nhất → cột đó ít giá trị')
    print("\nĐiểm này là của MỘT trọng tài LLM — đối chiếu với bench/quality/report.html (người chấm) "
          "trước khi tin; hai bên xếp khác nhau thì tin người.\n"
          "Giảm thiên vị vị trí: chấm mỗi đoạn hai lượt với thứ tự đảo rồi lấy trung bình (tốn gấp đôi).")


asyncio.run(main())
