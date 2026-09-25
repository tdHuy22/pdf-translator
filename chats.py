"""Phiên trò chuyện với LLM về một tài liệu: lưu lại, mở lại, đổi tên, xoá, xuất Markdown.

Mỗi tài liệu một file `data/docs/{id}/chats.json` (không nằm trong state.json: state.json được ghi lại sau mỗi lần
tô vàng, còn lịch sử trò chuyện dài dần). Server là nơi giữ lịch sử: giao diện chỉ gửi câu hỏi mới kèm id phiên, server
tự ghép lịch sử và tự ghi câu hỏi + câu trả lời — tải lại trang, mở từ máy khác đều thấy đúng một lịch sử.

    phiên   {"id", "title", "created", "updated", "messages": [tin nhắn…]}
    tin     {"id", "role": "user"|"assistant", "content", "ts",
             user:      "quote" (đoạn bôi đen), "page", "side", "note_ids", "context" (chế độ ngữ cảnh đã chọn)
             assistant: "model", "ctx" (ngữ cảnh đã gửi: chế độ, trang, ảnh), "stopped" (người dùng bấm Dừng)}

File này chỉ có hàm thuần + đọc/ghi file; endpoint nằm trong app.py."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

MAX_SESSIONS = 200
_locks: dict[str, threading.Lock] = {}


def _file(folder: Path) -> Path:
    return folder / "chats.json"


def load(folder: Path | None) -> list[dict]:
    try:
        data = json.loads(_file(folder).read_text("utf-8")) if folder else []
        return [s for s in data if isinstance(s, dict) and s.get("id")]
    except (OSError, ValueError):
        return []


def save(folder: Path | None, sessions: list[dict]) -> None:
    """Ghi nguyên tử. Thư mục tài liệu đã bị xoá (🗑 trong lúc LLM còn đang trả lời) → bỏ qua, đừng tạo lại."""
    if not folder or not folder.exists():
        return
    with _locks.setdefault(str(folder), threading.Lock()):
        tmp = _file(folder).with_name("chats.json.tmp")
        tmp.write_text(json.dumps(sessions[-MAX_SESSIONS:], ensure_ascii=False), "utf-8")
        os.replace(tmp, _file(folder))


def new(title: str = "") -> dict:
    now = time.time()
    return {"id": uuid.uuid4().hex[:10], "title": title, "created": now, "updated": now, "messages": []}


def title_of(question: str) -> str:
    t = " ".join(question.split())
    return t if len(t) <= 60 else t[:57].rsplit(" ", 1)[0] + "…"


def summary(s: dict) -> dict:
    """Một dòng trong danh sách phiên (không kèm tin nhắn)."""
    msgs = s.get("messages", [])
    return {"id": s["id"], "title": s.get("title") or "Phiên chưa có câu hỏi", "created": s.get("created", 0),
            "updated": s.get("updated", 0), "n": sum(m["role"] == "user" for m in msgs)}


def listing(sessions: list[dict]) -> list[dict]:
    return sorted((summary(s) for s in sessions), key=lambda x: -x["updated"])


def message(role: str, content: str, **extra) -> dict:
    return {"id": uuid.uuid4().hex[:8], "role": role, "content": content, "ts": time.time(),
            **{k: v for k, v in extra.items() if v not in (None, "", [], {})}}


def history(s: dict, keep: int = 8) -> list[dict]:
    """Lịch sử gửi cho LLM: `keep` lượt gần nhất, chỉ role + content (bỏ câu trả lời rỗng)."""
    out = [{"role": m["role"], "content": m["content"]} for m in s.get("messages", [])
           if m["role"] in ("user", "assistant") and m.get("content")]
    return out[-keep:]


def transcript(s: dict, limit_chars: int) -> str:
    """Cả phiên thành chữ để LLM tổng hợp; dài quá thì bỏ các lượt đầu (giữ lượt mới nhất)."""
    parts = []
    for m in s.get("messages", []):
        who = "Người đọc" if m["role"] == "user" else "Trợ giảng"
        head = f"### {who}" + (f" (trang {m['page'] + 1})" if m.get("page") is not None and m["role"] == "user" else "")
        quote = f"\n> {m['quote'][:300]}" if m.get("quote") else ""
        parts.append(f"{head}{quote}\n{m['content'].strip()}")
    out, used = [], 0
    for p in reversed(parts):
        if used + len(p) > limit_chars and out:
            out.append("[… các lượt đầu đã lược bớt …]")
            break
        out.append(p)
        used += len(p)
    return "\n\n".join(reversed(out))


def markdown(s: dict, doc_title: str) -> str:
    """Xuất một phiên thành Markdown (nút ⬇ trong danh sách phiên)."""
    out = [f"# {s.get('title') or 'Trò chuyện'}", "", f"_Tài liệu: {doc_title}_", ""]
    for m in s.get("messages", []):
        if m["role"] == "user":
            where = f" · trang {m['page'] + 1}" if m.get("page") is not None else ""
            out += [f"## 🙋 Câu hỏi{where}", ""]
            if m.get("quote"):
                out += ["> " + m["quote"].replace("\n", "\n> "), ""]
        else:
            model = f" ({m['model']})" if m.get("model") else ""
            out += [f"### 🤖 Trả lời{model}", ""]
        out += [m["content"].strip(), ""]
    return "\n".join(out).rstrip() + "\n"
