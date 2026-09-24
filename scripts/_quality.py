"""Phần dùng chung của bước chấm chất lượng: đọc kết quả `bench_quality.py` và chọn đúng cùng một bộ đoạn
cho trọng tài LLM (`judge_quality.py`) và cho người chấm (`quality_report.py`) — hai bên phải chấm cùng một
bộ đoạn thì mới đối chiếu được với nhau."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

OUT = ROOT / "bench" / "quality"
AXES = ("adequacy", "terminology", "fluency")


def load_runs() -> dict:
    """{(bài, trang, số hiệu đoạn): {hệ thống: bản dịch}} + chữ gốc, lấy từ các file bench_quality.py đã ghi."""
    cand, src = {}, {}
    for f in sorted(OUT.glob("*--*-p*.json")):
        d = json.loads(f.read_text())
        for it in d["items"]:
            key = (d["paper"], d["page"], it["id"])
            src[key] = {"src": it["src"], "table": it["table"]}
            if it["vi"]:
                cand.setdefault(key, {})[d["system"]] = it["vi"]
    return {"cand": cand, "src": src}


def pick(data: dict, systems: list[str], per_page: int = 6, min_chars: int = 120) -> list[dict]:
    """Các đoạn đem chấm: MỌI hệ thống đều có bản dịch (bỏ sót đoạn khó mà vẫn được chấm phần dễ thì điểm ảo),
    không phải ô bảng, đủ dài, tối đa `per_page` đoạn dài nhất mỗi trang."""
    pages: dict = {}
    for key, c in sorted(data["cand"].items()):
        info = data["src"][key]
        if set(systems) - set(c) or info["table"] or len(app.untag(info["src"])) < min_chars:
            continue
        pages.setdefault(key[:2], []).append({"key": key, "src": info["src"], "cand": {s: c[s] for s in systems}})
    out = []
    for items in pages.values():
        items.sort(key=lambda x: -len(x["src"]))      # đoạn dài phân biệt được model rõ hơn
        out += sorted(items[:per_page], key=lambda x: x["key"])
    return out
