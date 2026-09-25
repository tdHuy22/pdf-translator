"""Thử nghiệm dịch thật (gọi LLM) trên vài trang bench: so cách gọi model, lưu bản dịch vào bench/translations/
để bench bố cục (bench_text.py --render) dùng chữ tiếng Việt thật thay vì chữ gốc.

Chạy: .venv/bin/python scripts/bench_llm.py [A|B ...]
  A = API kiểu OpenAI, ngữ cảnh 4096 (như trước 19/09/2026)   B = API gốc Ollama, num_ctx theo tài liệu, JSON schema
"""
import asyncio
import collections
import json
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
app._uterms.update(blocked={}, prefer={})   # đo thuật toán, không để thuật ngữ người dùng đã dạy ảnh hưởng

PAGES = [("attention-is-all-you-need", 3), ("bert", 2), ("datasentinel", 8), ("datasentinel", 17)]
OUT = ROOT / "bench" / "translations"
_docs: dict[str, dict] = {}   # bài → {"title", "glossary"}: lập một lần, dùng cho mọi trang của bài đó


async def doc_info(cfg, path) -> dict:
    """Tên bài + bảng thuật ngữ tự lập (một lượt gọi LLM cho mỗi bài, như app vẫn làm)."""
    if path.stem not in _docs:
        doc = pymupdf.open(path)
        made = await app.build_glossary(cfg, doc)
        d = {"gloss": made["rows"] if made else []}
        app.apply_gloss(d)
        _docs[path.stem] = {"title": app.doc_title(doc), "glossary": d["glossary"]}
        print(f"  bảng thuật ngữ {path.stem}: {len(_docs[path.stem]['glossary'])} mục", flush=True)
    return _docs[path.stem]


def glossary_score(glossary: dict, blocks: list, out: dict) -> tuple[int, int]:
    """Bản dịch có dùng đúng cách dịch đã chốt không: (số lần đúng, số lần thuật ngữ xuất hiện)."""
    hit = total = 0
    for i, b in enumerate(blocks):
        vi = app.untag(out.get(i, "") or "").lower()
        if not vi:
            continue
        src = app.untag(b["text"]).lower()
        for en, want in glossary.items():
            if en.lower() in src:
                total += 1
                hit += want.lower() in vi
    return hit, total


async def run(mode: str, name: str, pno: int) -> dict:
    path = ROOT / "data" / f"{name}.pdf"
    cfg = app.Config()
    d = {"src": path, "out": pymupdf.open(path), "blocks": {}}
    blocks, _ = await app.page_blocks(d, pno, cfg)
    context = await app.block_context(d, [pno], cfg, blocks)   # nhận danh sách trang (nhóm trang gộp)
    info = await doc_info(cfg, path) if mode == "B" else {}
    if mode == "A":
        app._servers[cfg.base_url] = ("other", 4096)
    else:
        app._servers.pop(cfg.base_url, None)
    run = {"num_ctx": app.doc_num_ctx(pymupdf.open(path), cfg), "tokens": collections.Counter()}
    token = app._run.set(run)
    t = time.time()
    try:
        out, stats = await app.translate_blocks(cfg, blocks, context, app.system_prompt(cfg, info))
    finally:
        app._run.reset(token)
    kept_tags = sum(bool(app.TAG.search(out.get(i, ""))) for i, b in enumerate(blocks) if app.TAG.search(b["text"]))
    hit, total = glossary_score(info.get("glossary", {}), blocks, out)
    over = sum(len(app.untag(v)) > app.max_len(blocks[i]["text"], cfg, blocks[i].get("label") == "table") * 1.1
               for i, v in out.items())
    res = {"mode": mode, "paper": name, "page": pno + 1, "seconds": round(time.time() - t), "blocks": len(blocks),
           "unique": len({b["text"] for b in blocks}), "translated": len(out), "via_google": stats["google"],
           "errors": stats["errors"][:2], "tokens": dict(run["tokens"]), "num_ctx": run["num_ctx"] if mode == "B" else 4096,
           "tagged": sum(bool(app.TAG.search(b["text"])) for b in blocks), "kept_tags": kept_tags,
           "glossary_ok": f"{hit}/{total}", "over_max_len": over,
           "len_ratio": round(sum(len(app.untag(v)) for v in out.values())
                              / max(sum(len(app.untag(blocks[i]["text"])) for i in out), 1), 2)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}-p{pno + 1}-{mode}.json").write_text(json.dumps(
        {**res, "model": cfg.model, "items": [{"text": b["text"], "vi": out.get(i)} for i, b in enumerate(blocks)]},
        ensure_ascii=False, indent=1))
    return res


async def main():
    modes = sys.argv[1:] or ["A", "B"]
    for name, pno in PAGES:
        for mode in modes:
            r = await run(mode, name, pno)
            print(json.dumps(r, ensure_ascii=False), flush=True)


asyncio.run(main())
