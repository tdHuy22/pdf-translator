"""Lưu tài liệu xuống đĩa: khởi động lại app vẫn mở lại được bản dịch, tô vàng, bảng thuật ngữ.

Mỗi tài liệu một thư mục `data/docs/{id}/`:
    src.pdf     bản gốc người dùng tải lên (không bao giờ sửa)
    out.pdf     bản dịch đang dựng dở (trang chưa dịch vẫn là trang gốc)
    state.json  mọi thứ còn lại: tên, trang đã dịch, bản dịch từng đoạn, khối đã trích, tô vàng, thuật ngữ…

Ghi nguyên tử (file tạm + os.replace): tắt app giữa chừng thì còn bản cũ nguyên vẹn chứ không có file hỏng một nửa.
out.pdf ghi TRƯỚC state.json, và state.json chỉ ghi nhận trang đã có trong out.pdf trên đĩa (`disk_done`): tắt app
đúng lúc đang dịch thì mở lại thấy trang đó "chưa dịch", chứ không phải "đã dịch" mà ảnh vẫn là bản gốc."""
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).parent / "data" / "docs"
_ID = re.compile(r"[0-9a-f]{12}")
_locks: dict[str, threading.Lock] = {}   # một khoá mỗi tài liệu: endpoint đồng bộ chạy song song trong threadpool


def folder(doc_id: str) -> Path | None:
    """Thư mục của tài liệu; id lạ (không đúng 12 ký tự hex) → None, không bao giờ ra ngoài ROOT."""
    return ROOT / doc_id if _ID.fullmatch(doc_id or "") else None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_state(doc_id: str) -> dict | None:
    f = folder(doc_id)
    try:
        return json.loads((f / "state.json").read_text("utf-8")) if f else None
    except (OSError, ValueError):
        return None


def find(sha: str) -> str | None:
    """Tài liệu đã tải lên trước đây có đúng nội dung này (tải lại cùng file → mở lại bản cũ, không dịch lại)."""
    for f in ROOT.glob("*/state.json") if ROOT.exists() else ():
        st = _read_state(f.parent.name)
        if st and st.get("sha256") == sha and (f.parent / "src.pdf").exists():
            return f.parent.name
    return None


def create(doc_id: str, data: bytes) -> Path:
    f = folder(doc_id)
    f.mkdir(parents=True, exist_ok=True)
    _write(f / "src.pdf", data)
    return f / "src.pdf"


def save_pdf(d: dict) -> None:
    """Ghi bản dịch đang dựng. Gọi khi đang giữ d["lock"] (không để trang khác chèn vào giữa lúc ghi).
    garbage=4 (gộp cả stream trùng nội dung): mỗi trang dịch nhúng lại nguyên font, garbage=3 không gộp được nên bài
    15 trang ra 16,6 MB và mỗi lần ghi chậm dần tới ~1 s; garbage=4 còn 4 MB, ~0,6 s/lần, không tăng theo số trang
    (nó dọn luôn tài liệu trong bộ nhớ nên lần sau không phải gộp lại từ đầu). Đo 21/09/2026 trên
    attention-is-all-you-need; so với 30 s+ để dịch một trang thì không đáng kể."""
    done = set(d["done"])
    _write(folder(d["id"]) / "out.pdf", d["out"].tobytes(garbage=4, deflate=True))
    d["disk_done"] = done


def save_state(d: dict) -> None:
    """Ghi state.json. Nhẹ (vài chục KB), gọi sau mọi thay đổi: tô vàng, sửa thuật ngữ, dịch xong trang.
    Các dict được chụp lại bằng list(…items()) trước (một lệnh C, không bị luồng khác chen giữa): endpoint đồng bộ
    chạy trong threadpool, đang ghi mà trang khác vừa thêm vào là "dictionary changed size during iteration"."""
    done = set(d.get("disk_done", ()))
    st = {
        "v": 1,
        "name": d["name"],
        "sha256": d.get("sha256", ""),
        "pages": d["out"].page_count,
        "created": d.get("created", time.time()),
        "updated": time.time(),
        "done": sorted(done),
        # chỉ trang đã nằm trong out.pdf trên đĩa (xem chú thích đầu file)
        "trans": {str(p): {str(i): t for i, t in v.items()} for p, v in list(d["trans"].items()) if p in done},
        "placed": {str(p): {str(i): {"scale": x["scale"], "rect": list(x["rect"])} for i, x in v.items()}
                   for p, v in list(d["placed"].items()) if p in done},
        # khối đã trích (OCR bằng VLM mất 30–70 s/trang) — và số hiệu đoạn phải giữ nguyên thì tô vàng mới khớp
        "blocks": {str(p): {"key": list(k[0]), "sess": k[1], "blocks": b, "ocr": o}
                   for p, (k, b, o) in list(d["blocks"].items())},
        "marks": {str(p): list(v) for p, v in list(d["marks"].items()) if v},
    }
    for k in ("title", "gloss", "gloss_extra", "num_ctx"):
        if k in d:
            st[k] = d[k]
    with _locks.setdefault(d["id"], threading.Lock()):
        _write(folder(d["id"]) / "state.json", json.dumps(st, ensure_ascii=False).encode("utf-8"))
    d["created"] = st["created"]


def load(doc_id: str) -> dict | None:
    """Dựng lại tài liệu trong bộ nhớ từ đĩa (khoá, bộ đệm tạm… để app.py tự thêm). None nếu không có."""
    st, f = _read_state(doc_id), folder(doc_id)
    if not st or not (f / "src.pdf").exists():
        return None
    done = set(st.get("done", []))
    if done and (f / "out.pdf").exists():
        out = pymupdf.open("pdf", (f / "out.pdf").read_bytes())   # đọc hết vào RAM: ghi đè file sau đó vẫn an toàn
    else:
        out, done = pymupdf.open("pdf", (f / "src.pdf").read_bytes()), set()
    ints = lambda m: {int(k): v for k, v in m.items()}  # noqa: E731 — JSON chỉ có khoá chuỗi
    d = {
        "id": doc_id, "src": f / "src.pdf", "out": out, "name": st["name"], "sha256": st.get("sha256", ""),
        "created": st.get("created", time.time()), "done": done, "disk_done": set(done),
        "trans": {int(p): ints(v) for p, v in st.get("trans", {}).items() if int(p) in done},
        "placed": {int(p): {int(i): {"scale": x["scale"], "rect": tuple(x["rect"])} for i, x in v.items()}
                   for p, v in st.get("placed", {}).items() if int(p) in done},
        "blocks": {int(p): ((tuple(x["key"]), x["sess"]), x["blocks"], x["ocr"]) for p, x in st.get("blocks", {}).items()},
        "marks": ints(st.get("marks", {})),
    }
    for k in ("title", "gloss", "gloss_extra", "num_ctx"):
        if k in st:
            d[k] = st[k]
    return d


def list_docs() -> list[dict]:
    """Tài liệu đã lưu, mới dùng gần nhất trước."""
    out = []
    for f in ROOT.glob("*/state.json") if ROOT.exists() else ():
        st = _read_state(f.parent.name)
        if st:
            size = sum(p.stat().st_size for p in f.parent.iterdir() if p.is_file())
            out.append({"id": f.parent.name, "name": st["name"], "title": st.get("title", ""), "pages": st["pages"],
                        "done": len(st.get("done", [])), "marks": sum(len(v) for v in st.get("marks", {}).values()),
                        "updated": st.get("updated", 0), "size": size})
    return sorted(out, key=lambda x: -x["updated"])


def delete(doc_id: str) -> bool:
    f = folder(doc_id)
    if not f or not f.exists():
        return False
    shutil.rmtree(f)
    _locks.pop(doc_id, None)
    return True
