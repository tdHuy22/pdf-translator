"""So chất lượng dịch giữa nhiều hệ thống (Google Translate và các preset LLM) trên CÙNG một bộ trang.

    .venv/bin/python scripts/bench_quality.py --systems 9b            # lập bảng thuật ngữ (một lần, bằng 9b)
    .venv/bin/python scripts/bench_quality.py --systems google,2b,4b,9b

Điều kiện chạy được giữ giống hệt nhau cho mọi hệ thống, nếu không thì đang so nhầm thứ khác:
  - **Bảng thuật ngữ đóng băng**: lập MỘT lần (mặc định bằng `9b`), ghi ra `bench/quality/glossary.json`, mọi hệ
    thống sau đó dùng đúng bảng đó. Để mỗi model tự lập bảng thì model lập bảng kém bị trừ điểm hai lần.
  - **`google_fallback=False`**: đoạn nào model bỏ sót thì hiện ra là bỏ sót, không bị Google vá âm thầm.
  - `temperature=0`, không gộp trang (mỗi trang một lượt đo riêng), tắt thinking với server tự host.
  - Mỗi hệ thống chạy hết mọi trang rồi mới sang hệ thống sau: router llama.cpp nạp lại model khi đổi preset.
  - **Model không tắt được thinking** (preset `2b` của TUF là bản reasoning-distilled: bỏ qua cả
    `reasoning_effort=none` lẫn `chat_template_kwargs.enable_thinking=false`) được nới trần token đầu ra gấp
    `--think-scale` lần. Không nới thì phần suy nghĩ ăn hết trần, `content` trả về rỗng và MỌI đoạn đều tính là
    "bỏ sót" — đo ra 0 điểm vì hết chỗ viết chứ không phải vì dịch sai. Script tự dò bằng một lượt gọi nhỏ và ghi
    `thinks`/`think_scale` vào kết quả; token suy nghĩ vẫn là chi phí thật của model đó nên vẫn nằm trong cột giây.

Kết quả: `bench/quality/{system}--{paper}-p{n}.json` (từng đoạn, để chấm nghĩa ở bước sau) và bảng tóm tắt
các chỉ số ĐO ĐƯỢC (kỷ luật dịch, tốc độ) in ra màn hình + `bench/quality/summary.json`.
Các chỉ số này chỉ dùng để LOẠI, không xếp hạng được chất lượng nghĩa — phần đó do `judge_quality.py` và
`report.html` (người chấm) làm.
"""
import argparse
import asyncio
import collections
import json
import os
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
app._uterms.update(blocked={}, prefer={})   # đo thuật toán, không để thuật ngữ người dùng đã dạy ảnh hưởng

OUT = ROOT / "bench" / "quality"
# Bộ trang: văn xuôi + công thức giữa câu (attention, roformer), văn xuôi thuần (bert), ô bảng hẹp (datasentinel).
PAGES = [("attention-is-all-you-need", 3), ("bert", 2), ("roformer-rope", 3),
         ("datasentinel", 8), ("datasentinel", 17)]


def split_system(system: str) -> tuple[str, str]:
    """`tên=model` (tên dùng cho file kết quả, khi model có "/" hay ":" như nvidia/…:free) hay chỉ `model`."""
    name, _, model = system.partition("=")
    return (name, model) if model else (system, system)


def make_cfg(system: str, args) -> app.Config:
    """Cấu hình của một hệ thống. Mọi thứ ngoài (translator, model) đều giống nhau giữa các hệ thống. Key: --api-key,
    không có thì key đã nhớ của app cho đúng server đó (data/keys.json)."""
    common = dict(temperature=0.0, google_fallback=False, disable_thinking=True,
                  length_ratio=args.length_ratio, page_span=1, ocr_mode="off")
    if system == "google":
        return app.Config(translator="google", **common)
    return app.Config(translator="llm", base_url=args.base_url, api_key=args.api_key or app.remember(args.base_url, ""),
                      model=split_system(system)[1], **common)


_orig_chat = app.chat


async def probe_thinking(cfg: app.Config) -> bool:
    """Model có suy nghĩ không, dù đã bảo tắt: một lượt gọi nhỏ, xem server có trả `reasoning_content` không.
    llama.cpp tách phần suy nghĩ ra khỏi `content`, nên model vẫn nghĩ thì trần token đầu ra của app
    (≈1,6× độ dài bản dịch) bị phần suy nghĩ ăn sạch trước khi model kịp viết chữ nào."""
    if app.CC.is_claude(cfg.base_url):   # Claude Code: không có reasoning_content, --effort do app đặt
        return False
    import httpx

    body = app.no_think(cfg, cfg.base_url, {"model": cfg.model, "temperature": 0.0, "messages": [
        {"role": "user", "content": 'Dịch sang tiếng Việt, trả JSON {"items":[{"id":0,"text":"..."}]}: "The cat sits."'}]})
    body["max_tokens"] = 300
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(cfg.base_url.rstrip("/") + "/chat/completions", headers=headers, json=body)
        return bool((r.json()["choices"][0]["message"].get("reasoning_content") or "").strip())
    except Exception:  # noqa: BLE001 — dò không được thì coi như không nghĩ, chạy y như app thật
        return False


def use_think_scale(scale: float) -> None:
    """Nới trần token đầu ra cho model không tắt được thinking (scale = 1 → giữ nguyên hành vi của app)."""
    async def chat(cfg, system, payload, max_out=0, est_in=0):
        return await _orig_chat(cfg, system, payload, int(max_out * scale) if max_out else 0, est_in)

    app.chat = chat if scale != 1 else _orig_chat


async def load_page(paper: str, pno: int) -> tuple[dict, list[dict], dict]:
    """Khối của một trang + ngữ cảnh câu bị ngắt. Không gọi LLM: giống hệt nhau cho mọi hệ thống."""
    path = ROOT / "data" / f"{paper}.pdf"
    d = {"src": path, "out": pymupdf.open(path), "blocks": {}}
    cfg = app.Config(ocr_mode="off")
    blocks, _ = await app.page_blocks(d, pno, cfg)
    context = await app.block_context(d, [pno], cfg, blocks)
    return d, blocks, context


async def freeze_glossary(args) -> dict:
    """Bảng thuật ngữ + tên bài của từng bài, lập một lần rồi dùng lại cho mọi hệ thống (và mọi lần chạy lại)."""
    path = OUT / "glossary.json"
    store = json.loads(path.read_text()) if path.exists() else {}
    cfg = make_cfg(args.glossary_model, args)
    for paper in dict.fromkeys(p for p, _ in PAGES):
        if paper in store and not args.rebuild_glossary:
            continue
        doc = pymupdf.open(ROOT / "data" / f"{paper}.pdf")
        title = await asyncio.to_thread(app.doc_title, doc)
        made = await app.build_glossary(cfg, doc)
        if made is None:
            sys.exit(f"Không lập được bảng thuật ngữ cho {paper} (LLM lỗi) — chưa chạy bench được")
        d = {"gloss": made["rows"]}
        app.apply_gloss(d)
        store[paper] = {"title": title, "glossary": d["glossary"], "by": args.glossary_model}
        print(f"  [glossary] {paper}: {len(d['glossary'])} thuật ngữ (bằng {args.glossary_model}) – {title[:60]}")
        path.write_text(json.dumps(store, ensure_ascii=False, indent=1))
    return store


def metrics(cfg: app.Config, blocks: list[dict], out: dict[int, str]) -> dict:
    """Chỉ số kỷ luật dịch: đo được bằng máy, đủ để LOẠI một model (mất công thức, bỏ sót đoạn, tràn khung)."""
    tagged = [i for i, b in enumerate(blocks) if app.PLACEHOLDER.search(b["text"])]
    styled = [i for i, b in enumerate(blocks) if app.TAG.search(b["text"])]
    over = src_len = vi_len = 0
    for i, vi in out.items():
        tight = blocks[i].get("label") == "table"
        over += len(app.untag(vi)) > app.max_len(blocks[i]["text"], cfg, tight) * 1.1
        src_len += len(app.untag(blocks[i]["text"]))
        vi_len += len(app.untag(vi))
    return {
        "blocks": len(blocks), "translated": len(out), "missing": len(blocks) - len(out),
        # keep_formulas() BỎ bản dịch nào làm mất {vN} → đoạn có công thức mà thiếu = model đánh rơi công thức
        "formula_blocks": len(tagged), "formula_lost": sum(i not in out for i in tagged),
        "styled_blocks": len(styled), "styled_kept": sum(bool(app.TAG.search(out.get(i, ""))) for i in styled),
        "over_max_len": over, "len_ratio": round(vi_len / max(src_len, 1), 2),
    }


def glossary_score(glossary: dict, blocks: list[dict], out: dict[int, str]) -> str:
    """Bản dịch có dùng đúng cách dịch đã chốt không: (số lần đúng / số lần thuật ngữ xuất hiện)."""
    hit = total = 0
    for i, b in enumerate(blocks):
        if not (vi := app.untag(out.get(i, "") or "").lower()):
            continue
        src = app.untag(b["text"]).lower()
        for en, want in glossary.items():
            if en.lower() in src:
                total += 1
                hit += want.lower() in vi
    return f"{hit}/{total}"


async def run_one(system: str, paper: str, pno: int, info: dict, args, think: dict) -> dict:
    cfg = make_cfg(system, args)
    d, blocks, context = await load_page(paper, pno)
    doc = {"title": info["title"], "glossary": info["glossary"]}
    system_prompt = app.system_prompt(cfg, doc)
    cfg = cfg.model_copy(update={"glossary": app.glossary_text(cfg, doc)})
    run = {"num_ctx": 0, "tokens": collections.Counter()}
    token = app._run.set(run)
    t0 = time.time()
    err = ""
    try:
        out, stats = await app.translate_blocks(cfg, blocks, context, system_prompt)
    except Exception as e:  # noqa: BLE001 — google_fallback=False → LLM lỗi ném HTTPException; ghi lại rồi chạy tiếp
        out, stats, err = {}, {"llm": 0, "google": 0, "errors": [repr(e)[:300]]}, repr(e)[:300]
    finally:
        app._run.reset(token)
    secs = round(time.time() - t0, 1)
    tok = dict(run["tokens"])
    res = {"system": split_system(system)[0], "paper": paper, "page": pno + 1, "seconds": secs, **think,
           **metrics(cfg, blocks, out), "glossary_ok": glossary_score(info["glossary"], blocks, out),
           "tokens": tok, "tok_per_s": round(tok.get("completion", 0) / max(secs, 0.1), 1),
           "errors": stats["errors"][:2], "error": err}
    (OUT / f"{split_system(system)[0]}--{paper}-p{pno + 1}.json").write_text(json.dumps(
        {**res, "items": [{"id": i, "src": b["text"], "vi": out.get(i), "table": b.get("label") == "table"}
                          for i, b in enumerate(blocks)]}, ensure_ascii=False, indent=1))
    return res


ROW = "{system:8} {paper:26} {page:>4} {seconds:>7} {tok_per_s:>7} {translated:>4}/{blocks:<4} {missing:>4} " \
      "{formula_lost:>4}/{formula_blocks:<3} {over_max_len:>4} {len_ratio:>5} {glossary_ok:>8}"
HEAD = ROW.format(system="hệ thống", paper="bài", page="tr.", seconds="giây", tok_per_s="tok/s", translated="dịch",
                  blocks="đoạn", missing="sót", formula_lost="mất", formula_blocks="CT", over_max_len="tràn",
                  len_ratio="dài", glossary_ok="thuật ngữ")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="google,2b,4b,9b", help="google hoặc tên preset/model trên --base-url")
    ap.add_argument("--base-url", default=os.environ.get("VP_TUF_URL", "http://localhost:8080/v1"), help="server LLM (mặc định: TUF)")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--glossary-model", default="9b", help="model lập bảng thuật ngữ đóng băng")
    ap.add_argument("--rebuild-glossary", action="store_true")
    ap.add_argument("--length-ratio", type=float, default=1.3)
    ap.add_argument("--pages", default="", help='vd "bert:3,attention-is-all-you-need:4" (số trang từ 1)')
    ap.add_argument("--force", action="store_true", help="chạy lại cả những trang đã có kết quả")
    ap.add_argument("--think-scale", type=float, default=4.0,
                    help="nhân trần token đầu ra với model không tắt được thinking (1 = đo đúng như app thật)")
    args = ap.parse_args()

    global PAGES
    if args.pages:
        PAGES = [(p.rsplit(":", 1)[0], int(p.rsplit(":", 1)[1]) - 1) for p in args.pages.split(",")]
    OUT.mkdir(parents=True, exist_ok=True)
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]

    print(f"Bảng thuật ngữ đóng băng (lập bằng {args.glossary_model}):")
    store = await freeze_glossary(args)

    rows = []
    for system in systems:                      # hết trang của một hệ thống rồi mới sang hệ thống sau
        think = {"thinks": False, "think_scale": 1.0}
        if system != "google":
            think["thinks"] = await probe_thinking(make_cfg(system, args))
            think["think_scale"] = args.think_scale if think["thinks"] else 1.0
        use_think_scale(think["think_scale"])
        note = f"   (không tắt được thinking → nới trần đầu ra ×{think['think_scale']:g})" if think["thinks"] else ""
        print(f"\n=== {system} ==={note}\n{HEAD}")
        for paper, pno in PAGES:
            f = OUT / f"{split_system(system)[0]}--{paper}-p{pno + 1}.json"
            if f.exists() and not args.force:
                r = json.loads(f.read_text())
                r.pop("items", None)
                print(ROW.format(**r) + "   (đã có)")
            else:
                r = await run_one(system, paper, pno, store[paper], args, think)
                print(ROW.format(**r) + ("   ⚠️ " + r["error"] if r["error"] else ""))
            rows.append(r)
    (OUT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n=== Tổng theo hệ thống ===")
    print(f"{'hệ thống':10} {'giây/trang':>10} {'tok/s':>6} {'sót':>5} {'mất CT':>7} {'tràn':>5} {'dài':>5} {'thuật ngữ':>10}")
    for system in systems:
        rs = [r for r in rows if r["system"] == split_system(system)[0]]
        hit = sum(int(r["glossary_ok"].split("/")[0]) for r in rs)
        tot = sum(int(r["glossary_ok"].split("/")[1]) for r in rs)
        n = max(len(rs), 1)
        print(f"{system:10} {sum(r['seconds'] for r in rs) / n:10.1f} "
              f"{sum(r['tok_per_s'] for r in rs) / n:6.1f} "
              f"{sum(r['missing'] for r in rs):5} {sum(r['formula_lost'] for r in rs):7} "
              f"{sum(r['over_max_len'] for r in rs):5} "
              f"{sum(r['len_ratio'] for r in rs) / n:5.2f} {f'{hit}/{tot}':>10}")


asyncio.run(main())
