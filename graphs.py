"""Sơ đồ bài báo của từng tài liệu, dùng paper-graph (../paper-graph) như thư viện.

Mỗi tài liệu thêm vài file trong `data/docs/{id}/`:
    paper.blocks.json   các đoạn của bài theo định dạng paper-graph, lấy từ đúng các khối VietPaper đã trích (cả khối
                        OCR), nên đoạn `p{n}-b{k}` là đoạn số k-1 của trang n-1 trong VietPaper: bản dịch, vị trí đoạn
                        trên hai khung tra thẳng theo id
    paper.ids.json      dấu của từng khối lúc xuất (id → băm chữ thô): khối của trang đổi về sau (đổi cách OCR, trích
                        lại) thì id không còn khớp số hiệu đoạn, bản dịch và cuộn tới đoạn bị tắt cho trang đó; kèm chữ
                        của từng công thức trong khối, để thay chỗ giữ chỗ `{vN}` còn trong bản dịch
    paper.graph.json    sơ đồ (dựng bằng `paper_graph.build`, sửa trong trình xem); bản nháp `paper.graph.draft.json`
    paper.layout.json   toạ độ ô người dùng đã kéo

Không cài paper-graph (`pip install -e ../paper-graph`) thì app vẫn chạy, tab Sơ đồ báo thiếu thư viện.
Chốt chặn ở `.agent/threat-model.md` mục 2026-10-23 (TM-14 … TM-18)."""
import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import store

try:
    from paper_graph import build as pg_build
    from paper_graph import edits as pg_edits
    from paper_graph import extract as pg_extract
    from paper_graph import serve as pg_serve
    from paper_graph import view as pg_view
    from paper_graph.files import InputError
    from paper_graph.text import squash
    AVAILABLE = True
except ImportError:  # chưa cài: route trả 503, giao diện báo
    AVAILABLE = False
    InputError = Exception

OPENROUTER = "https://openrouter.ai/api/v1"
FREE_MODEL = re.compile(r"[a-z0-9._-]+/[a-z0-9._:-]+:free")   # TM-15: chỉ model miễn phí
MAX_USD = 0.001     # model :free giá 0 nên lượt nào cũng ước 0 USD; model đổi sang có giá thì `build` từ chối ngay
MAX_BODY = 64 * 1024
ASSETS = "/pg/"     # app phục vụ viewer.js, viewer.css của paper-graph ở đây
LOG_KEEP = 200
_SENT = re.compile(r"(?<=[.!?])\s+(?=\S)")
_SLOT = re.compile(r"\{\s*[vV]\s*(\d+)\s*\}")   # chỗ giữ chỗ công thức, như app.PLACEHOLDER


@dataclass
class Paths:
    blocks: Path
    ids: Path
    graph: Path
    draft: Path


def paths(doc_id: str) -> Paths | None:
    f = store.folder(doc_id)   # TM-17: chỉ id 12 ký tự hex, tên file cố định
    if f is None:
        return None
    return Paths(f / "paper.blocks.json", f / "paper.ids.json", f / "paper.graph.json", f / "paper.graph.draft.json")


def _mark(b: dict) -> str:
    return hashlib.sha256(str(b.get("text", "")).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- xuất các đoạn
def export_blocks(app_mod, src: Path, given: dict[int, tuple[list, bool]], p: Paths) -> dict:
    """Ghi paper.blocks.json từ khối VietPaper đã trích (mọi trang, `given`), và dấu của từng khối. Chạy trong thread:
    model bố cục chạy lại trên từng trang để lấy vùng hình, bảng (~0,6 giây mỗi trang)."""
    import pymupdf  # noqa: PLC0415 - có sẵn trong venv của VietPaper

    doc = pg_extract.extract(src, app=app_mod, given=given)
    pdf = pymupdf.open(src)
    marks, formulas, chars = {}, {}, {}
    for b in doc["blocks"]:
        n, k = (int(x) for x in re.fullmatch(r"p(\d+)-b(\d+)", b["id"]).groups())
        raw = given[n - 1][0][k - 1]
        marks[b["id"]] = _mark(raw)
        if raw.get("formulas"):
            if n not in chars:
                chars[n] = pg_extract.char_map(app_mod, pdf[n - 1])
            formulas[b["id"]] = ["".join("".join(chars[n].get(tuple(key), "") for key in f.get("chars", ())).split())
                                 for f in raw["formulas"]]
    store._write(p.blocks, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    store._write(p.ids, json.dumps({"marks": marks, "formulas": formulas}, ensure_ascii=False).encode("utf-8"))
    return doc


def _ids(p: Paths) -> tuple[dict, dict]:
    """(dấu từng khối, chữ các công thức của từng khối) lúc xuất; file thiếu hay hỏng thì rỗng."""
    try:
        doc = json.loads(p.ids.read_text("utf-8"))
        return dict(doc["marks"]), dict(doc.get("formulas") or {})
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}


def keep_old(p: Paths) -> None:
    """Trước khi xuất lại các đoạn để dựng lại: giữ bản đang khớp với sơ đồ hiện có. Lần dựng không đạt thì `restore`
    đặt lại, nếu không sơ đồ cũ (vẫn là paper.graph.json) bị so với các đoạn mới, lệch neo, không mở được."""
    for f in (p.blocks, p.ids):
        old = f.with_name(f.name + ".prev")
        if f.exists() and p.graph.exists():
            store._write(old, f.read_bytes())
        else:
            old.unlink(missing_ok=True)


def restore(p: Paths, ok: bool) -> None:
    for f in (p.blocks, p.ids):
        old = f.with_name(f.name + ".prev")
        if old.exists():
            if not ok:
                store._write(f, old.read_bytes())
            old.unlink(missing_ok=True)


def _current(d: dict, page0: int) -> list | None:
    """Khối VietPaper đang dùng cho trang (số trang đếm từ 0), hay None nếu chưa trích."""
    cached = d["blocks"].get(page0)
    return cached[1] if cached else d.get(("layer", page0))


def stale_pages(d: dict, p: Paths) -> list[int]:
    """Trang (đếm từ 1) có khối đã đổi so với lúc xuất: id ở đó không còn khớp số hiệu đoạn của VietPaper."""
    marks, _ = _ids(p)
    bad = set()
    for bid, mark in marks.items():
        n, k = (int(x) for x in re.fullmatch(r"p(\d+)-b(\d+)", bid).groups())
        cur = _current(d, n - 1)
        if cur is not None and (k > len(cur) or _mark(cur[k - 1]) != mark):
            bad.add(n)
    return sorted(bad)


# ---------------------------------------------------------------- bản dịch câu trích
def _sentences(text: str) -> list[str]:
    return [s for s in _SENT.split(" ".join(text.split())) if s]


def translation(d: dict, marks: dict, src_text: str, block: str | None, quote: str,
                formulas: list[str] | None = None) -> tuple[str, bool] | None:
    """Bản dịch của câu trích: các câu cùng vị trí trong bản dịch của đoạn nếu hai bản cùng số câu (model dịch giữ thứ
    tự câu), không thì cả đoạn (`True` ở phần tử thứ hai). None khi đoạn chưa dịch hay id không còn khớp. Chỗ giữ chỗ
    công thức `{vN}` (bản dịch lưu nguyên, ảnh trang dịch mới dán công thức vào) thay bằng chữ của công thức."""
    m = re.fullmatch(r"p(\d+)-b(\d+)", block or "")
    if not m:
        return None
    n, k = int(m.group(1)), int(m.group(2))
    cur = _current(d, n - 1)
    vi = d["trans"].get(n - 1, {}).get(k - 1) if n - 1 in d["done"] else None
    if not vi or cur is None or k > len(cur) or marks.get(block) != _mark(cur[k - 1]):
        return None
    vi = re.sub(r"</?[bi]>", "", vi)
    fx = formulas or []
    vi = _SLOT.sub(lambda mt: fx[int(mt.group(1)) - 1] if 0 < int(mt.group(1)) <= len(fx) and fx[int(mt.group(1)) - 1]
                   else "…", vi)
    src, dst = _sentences(src_text), _sentences(vi)
    if len(src) == len(dst) > 1:
        spans, at = [], 0
        for s in src:
            spans.append((at, at + len(squash(s))))
            at = spans[-1][1]
        q = squash(quote)
        start = "".join(squash(s) for s in src).find(q) if q else -1
        if start >= 0:   # các câu có phần chồng lên câu trích
            hit = [i for i, (a, z) in enumerate(spans) if a < start + len(q) and z > start]
            return " ".join(dst[hit[0]:hit[-1] + 1]), False
    return vi, True


def with_translations(d: dict, p: Paths, nodes: dict) -> dict:
    """Thêm `vi`, `vi_whole` vào neo của các ô (dữ liệu của trình xem)."""
    marks, formulas = _ids(p)
    try:
        texts = {b["id"]: b["text"] for b in json.loads(p.blocks.read_text("utf-8"))["blocks"]}
    except (OSError, ValueError, KeyError, TypeError):
        return nodes
    for n in nodes.values():
        for a in n.get("anchors", ()):
            b = a.get("block") or ""
            got = translation(d, marks, texts.get(b, ""), a.get("block"), a.get("quote", ""), formulas.get(b))
            if got:
                a["vi"], a["vi_whole"] = got
    return nodes


# ---------------------------------------------------------------- trình xem, sửa
_apps: dict[str, tuple[tuple, "pg_serve.App"]] = {}
_apps_lock = threading.Lock()


def viewer(doc_id: str, src: Path) -> "pg_serve.App":
    """Trạng thái trình xem của một bài (token sửa, ảnh hình đã cắt). Dựng lại khi blocks.json hay sơ đồ được thay
    bằng file mới (dựng lại); sửa trong trình xem ghi đè cùng file nên ảnh đã cắt vẫn dùng tiếp."""
    p = paths(doc_id)
    key = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in (p.blocks, src))
    with _apps_lock:
        hit = _apps.get(doc_id)
        if hit and hit[0] == key:
            return hit[1]
        a = pg_serve.App(p.graph, p.blocks, src, images=True, crops=False)
        _apps[doc_id] = (key, a)
        return a


def forget(doc_id: str) -> None:
    with _apps_lock:
        _apps.pop(doc_id, None)


def page_html(doc_id: str, d: dict) -> str:
    p = paths(doc_id)
    a = viewer(doc_id, Path(d["src"]))
    with a.lock:
        data, version = a.data()
    data["nodes"] = with_translations(d, p, data["nodes"])
    return pg_view.page(dict(data, embed=True),
                        {"token": a.token, "version": version, "api": f"/api/{doc_id}/graph/"}, assets=ASSETS)


def change(doc_id: str, d: dict, what: str, body: dict, token: str) -> dict:
    """Sửa ô (`edit`) hay kéo ô (`move`) qua paper-graph: kiểm `check`, số có trong bài, `version` (TM-17)."""
    a = viewer(doc_id, Path(d["src"]))
    if not hmac.compare_digest(token.encode("utf-8", "replace"), a.token.encode("ascii")):
        raise PermissionError("thiếu hay sai token của trình xem: tải lại tab Sơ đồ")
    reply = (a.edit if what == "edit" else a.move)(body)
    reply["nodes"] = with_translations(d, paths(doc_id), reply["nodes"])
    return reply


# ---------------------------------------------------------------- dựng sơ đồ (nền, một lần mỗi lúc cho cả app)
JOB: dict = {}
_job_lock = threading.Lock()


def job_view(doc_id: str) -> dict | None:
    with _job_lock:
        if JOB.get("doc") != doc_id:
            return {"running": True, "other": True} if JOB.get("running") else None
        return {k: (list(v) if k == "log" else v) for k, v in JOB.items() if k != "doc"}


def claim(doc_id: str, model: str) -> bool:
    """Giữ chỗ lần dựng (TM-15); False nếu đang có lần dựng khác."""
    with _job_lock:
        if JOB.get("running"):
            return False
        JOB.clear()
        JOB.update(doc=doc_id, running=True, model=model, log=[], ok=None, out="", error="", t0=time.time())
        return True


def say(line: str, key: str = "") -> None:
    line = pg_build.redact(str(line), key) if AVAILABLE else str(line)   # TM-16
    with _job_lock:
        JOB.setdefault("log", []).append(line[:500])
        del JOB["log"][:-LOG_KEEP]


def finish(**kw) -> None:
    with _job_lock:
        JOB.update(running=False, t1=time.time(), **kw)


def run_build(doc_id: str, model: str, key: str) -> tuple[bool, str]:
    """Gọi `paper_graph.build` (chạy trong thread). Đạt thì paper.graph.json, chưa đạt thì bản nháp. Đã có sơ đồ thì
    dựng lại kiểu `--force`: ô người dùng đã khoá giữ nguyên, bản cũ chép sang paper.graph.old.json."""
    p = paths(doc_id)
    fallbacks = [m for m in pg_build.DEFAULT_FALLBACKS if m != model]
    res, out = pg_build.build(p.blocks, model, 3, MAX_USD, force=p.graph.exists(), say=lambda s: say(s, key),
                              key=key, allow_training=True, fallbacks=fallbacks, workers=4)
    forget(doc_id)
    return out == p.graph, out.name


def status(doc_id: str, d: dict) -> dict:
    p = paths(doc_id)
    return {"available": AVAILABLE, "graph": p.graph.exists(), "draft": p.draft.exists(),
            "blocks": p.blocks.exists(), "stale": stale_pages(d, p) if p.graph.exists() else [],
            "job": job_view(doc_id), "model": pg_build.DEFAULT_MODEL if AVAILABLE else "",
            "pages": d["out"].page_count}
