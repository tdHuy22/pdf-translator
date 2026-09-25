"""Ghi chú của người đọc: cho cả bài, cho một trang, hoặc gắn vào một vệt tô vàng.

Mỗi tài liệu một danh sách `d["notes"]` (lưu trong state.json cùng bản dịch, xem store.py):
    {"id", "text", "page": int | None, "mark_id": str | None, "quote", "source": "user" | "llm",
     "meta": {"question", "model"}, "created", "updated"}
- page = None, mark_id = None: ghi chú cho cả bài. Riêng id "general" là ô "ghi chú chung" trên giao diện.
- mark_id: gắn vào vệt tô đó. Bỏ vệt tô KHÔNG xoá ghi chú (ghi chú là công sức người đọc) — ghi chú mất vệt thì
  vẫn giữ trang và đoạn trích `quote`, hiện như ghi chú của trang.
- text là Markdown (ghi chú lưu từ câu trả lời LLM có công thức, bảng) — giao diện dựng bằng render.js.

File này chỉ có hàm thuần; endpoint nằm trong app.py."""
import time
import uuid

import pymupdf
from pydantic import BaseModel

GENERAL = "general"
MAX_TEXT = 20000                     # một ghi chú; chặn lỡ tay dán cả bài báo vào


class Note(BaseModel):
    id: str = ""                     # rỗng = ghi chú mới (server đặt id)
    text: str = ""
    page: int | None = None
    mark_id: str | None = None
    quote: str = ""
    source: str = "user"
    meta: dict = {}


def upsert(notes: list[dict], n: Note, pages: int) -> dict | None:
    """Thêm (id rỗng; id "general" lần đầu) hoặc sửa chữ một ghi chú; trả về ghi chú đã lưu.
    None: sửa một ghi chú không còn (đã xoá ở tab khác) — đừng lặng lẽ tạo bản trùng với id khác.
    `pages`: số trang, để chặn số trang lạ."""
    now = time.time()
    page = n.page if n.page is not None and 0 <= n.page < pages else None
    if n.id and (old := next((x for x in notes if x["id"] == n.id), None)):
        old.update(text=n.text[:MAX_TEXT], updated=now)
        return old                   # sửa chỉ đổi chữ: vị trí, nguồn, đoạn trích giữ như lúc tạo
    if n.id and n.id != GENERAL:
        return None
    note = {"id": n.id if n.id == GENERAL else uuid.uuid4().hex[:10], "text": n.text[:MAX_TEXT],
            "page": None if n.id == GENERAL else page, "mark_id": None if n.id == GENERAL else n.mark_id,
            "quote": n.quote[:2000], "source": "llm" if n.source == "llm" else "user",
            "meta": {k: str(v)[:2000] for k, v in (n.meta or {}).items() if k in ("question", "model")},
            "created": now, "updated": now}
    notes.append(note)
    return note


def ordered(notes: list[dict]) -> list[dict]:
    """Ghi chú chung trước, rồi ghi chú cả bài, rồi theo trang; cùng chỗ thì theo thời gian tạo."""
    return sorted(notes, key=lambda x: (x["id"] != GENERAL, x["page"] is not None, x["page"] or 0, x["created"]))


def _where(note: dict) -> str:
    if note["id"] == GENERAL:
        return "ghi chú chung cho cả bài"
    return f"trang {note['page'] + 1}" if note["page"] is not None else "cả bài"


def context(notes: list[dict], ids: list[str], budget_chars: int) -> str:
    """Phần ghi chú gửi kèm câu hỏi ("Hỏi lại" một ghi chú): đúng các ghi chú được chọn, cắt theo `budget_chars`."""
    parts, left = [], budget_chars
    for note in (x for x in ordered(notes) if x["id"] in set(ids)):
        head = f"[Ghi chú của người đọc — {_where(note)}"
        head += f", về đoạn: “{note['quote'][:300]}”]" if note["quote"] else "]"
        body = note["text"][:max(left - len(head), 0)]
        if not body:
            break
        parts.append(f"{head}\n{body}")
        left -= len(head) + len(body)
    return "\n\n".join(parts)


def markdown(title: str, name: str, notes: list[dict]) -> str:
    """Toàn bộ ghi chú thành một file Markdown (nút "Xuất .md")."""
    out = [f"# Ghi chú — {title or name}", ""]
    if title and name:
        out += [f"_Tệp: {name}_", ""]
    last = object()
    for note in ordered(notes):
        if not note["text"].strip():
            continue
        where = "Ghi chú chung" if note["id"] == GENERAL else \
            (f"Trang {note['page'] + 1}" if note["page"] is not None else "Cả bài")
        if where != last:
            out += [f"## {where}", ""]
            last = where
        if note["quote"]:
            out += ["> " + note["quote"].replace("\n", "\n> "), ""]
        if note["source"] == "llm":
            q, m = note["meta"].get("question"), note["meta"].get("model")
            out += [f"_Câu trả lời của LLM{f' ({m})' if m else ''}{f' cho câu hỏi: {q}' if q else ''}_", ""]
        out += [note["text"].strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def mark_rects(m: dict) -> list:
    """Khung của vệt tô trên BẢN DỊCH (PDF tải về là bản dịch): vệt tô ở bản dịch thì khung của chính nó, tô ở bản
    gốc thì khung cụm/câu tương ứng bên bản dịch."""
    return m["rects"] if m["side"] == "translated" else m["peer"]


def plan(marks: dict, notes: list[dict]) -> tuple[dict[str, str], dict[int, str]]:
    """Ghi chú nào vào đâu trong PDF tải về: {id vệt tô: chữ} cho vệt có khung trên bản dịch (thành nội dung popup
    của highlight đầu tiên của vệt đó), và {trang: chữ} cho phần còn lại — ghi chú của trang, của vệt đã bỏ hoặc
    không có khung bên bản dịch, và ghi chú cả bài (trang 1) — thành biểu tượng ghi chú ở lề trên trang."""
    drawn = {m["id"] for ms in marks.values() for m in ms if mark_rects(m)}
    by_mark: dict[str, list[dict]] = {}
    loose: dict[int, list[dict]] = {}
    for note in ordered(notes):
        if not note["text"].strip():
            continue
        if note["mark_id"] in drawn:
            by_mark.setdefault(note["mark_id"], []).append(note)
        else:
            loose.setdefault(note["page"] if note["page"] is not None else 0, []).append(note)
    text = lambda ns, quote: "\n\n".join(("“" + x["quote"][:200] + "”\n" if quote and x["quote"] else "")  # noqa: E731
                                          + x["text"].strip() for x in ns)
    return ({k: text(v, False) for k, v in by_mark.items()}, {k: text(v, True) for k, v in loose.items()})


def add_loose(doc: pymupdf.Document, loose: dict[int, str]) -> None:
    """Biểu tượng ghi chú ở góc trên phải của trang, nội dung là các ghi chú không gắn được vào highlight nào."""
    for n, text in sorted(loose.items()):
        if n >= doc.page_count:
            continue
        page = doc[n]                                  # giữ tham chiếu trang, không thì annot "not bound"
        annot = page.add_text_annot(pymupdf.Point(page.rect.width - 28, 14), text, icon="Note")
        annot.set_info(title="VietPaper")
        annot.update()
