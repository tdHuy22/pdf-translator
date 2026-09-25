"""Thử căn vệt tô bằng LLM trên một tài liệu đã dịch (chỉ đọc, không sửa tài liệu): chọn ngẫu nhiên cụm vài chữ
trong các câu thật, hỏi LLM cụm tương ứng ở bản kia, in ra để người đọc chấm, kèm thời gian và tỉ lệ phải lùi về
tô cả câu. Dựng chữ của đoạn giống hệt giao diện (lớp chữ /layer, tách câu như sentSpans trong static/app.js).

    .venv/bin/python scripts/bench_align.py --doc 522c3e0499fe            # model free trên OpenRouter, 20 cụm
    .venv/bin/python scripts/bench_align.py --doc … --base-url http://100.64.0.1:8080/v1 --model 4b   # TUF (dự phòng)
    .venv/bin/python scripts/bench_align.py --gold bench/align_gold.json --model 4b   # chấm tự động theo đáp án

bench/align_gold.json: 60 cụm (từ hai lượt chạy ngẫu nhiên trên bài FIDES) có đáp án ghi tay — mỗi cụm một hoặc
vài cụm chấp nhận được. Đáp án là đoạn LIỀN nhỏ nhất ở bản kia chứa bản dịch của MỌI chữ đã bôi đen (bản dịch chen
chữ khác vào giữa thì đáp án gồm luôn chữ đó: "perform future" → "thực hiện các lời gọi công cụ sau này").
"""
import argparse
import json
import pathlib
import random
import re
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

import app as A

SENT_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)|[。！？]")
NO_SPLIT = re.compile(r"(?:^|[\s(])(?:[A-Za-z]|et al|e\.g|i\.e|cf|vs|Fig|Eq|Sec|Tab|No|Dr|Mr|Ms|Prof|Ref)\.$")


def sent_spans(text: str) -> list[list[int]]:
    """Bản Python của sentSpans (static/app.js) — phải giữ giống nhau."""
    out, at = [], 0
    for m in SENT_END.finditer(text):
        if NO_SPLIT.search(text[max(0, m.start() - 10):m.start() + 1]):
            continue
        out.append([at, m.end()])
        at = m.end()
    if at < len(text):
        out.append([at, len(text)])
    for i in range(len(out) - 1, 0, -1):
        if not re.search(r"[^\W\d_]", text[out[i][0]:out[i][1]]):
            out[i - 1][1] = out[i][1]
            del out[i]
    return out or [[0, len(text)]]


def block_texts(layer: dict) -> dict[int, str]:
    """Chữ từng đoạn ghép từ các dòng, như blockText() của giao diện."""
    out: dict[int, str] = {}
    for ln in layer["lines"]:
        if ln.get("b") is None:
            continue
        t = out.get(ln["b"], "")
        if t and not t[-1].isspace() and not ln.get("j"):
            t += " "
        out[ln["b"]] = t + ln["text"]
    return out


# Người bôi đen thường chọn một thuật ngữ, cụm danh từ hay mệnh đề — không mở đầu/kết thúc bằng hư từ
EDGE = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "with", "for", "by", "that", "is", "are", "we", "as",
        "at", "from", "be", "this", "it", "its", "và", "của", "các", "là", "được", "để", "cho", "trong", "với",
        "những", "một", "có", "này", "đó", "khi", "thì", "mà", "như", "từ", "ở", "bị", "sẽ"}


def pick_phrase(rng: random.Random, sent: str, vi: bool) -> tuple[int, int] | None:
    words = [m.span() for m in re.finditer(r"[^\s]+", sent)]
    good = lambda w: re.fullmatch(r"[(\[\"“]?[^\W\d_][\w\-’']*[)\]\"”.,;:]?", sent[w[0]:w[1]]) and \
        sent[w[0]:w[1]].strip("()[]\"“”.,;:").lower() not in EDGE      # noqa: E731
    for _ in range(20):
        k = rng.randint(2, 5) if vi else rng.randint(1, 4)    # tiếng Việt: một từ thường là 2 âm tiết
        if len(words) < k + 4:
            return None
        i = rng.randrange(0, len(words) - k + 1)
        if good(words[i]) and good(words[i + k - 1]) and all(re.search(r"[^\W\d_]", sent[a:b]) for a, b in words[i:i + k]):
            s, e = words[i][0], words[i + k - 1][1]
            while e > s and sent[e - 1] in ".,;:)]\"”":
                e -= 1
            while s < e and sent[s] in "([\"“":
                s += 1
            return s, e
    return None


def run_gold(args, cfg):
    """Chấm theo đáp án: đúng (trùng một đáp án), một phần (chồng lên đáp án), sai (không chồng), không tìm thấy."""
    A.store.ROOT = pathlib.Path(tempfile.mkdtemp(prefix="bench-align-"))   # tài liệu trống tạm, bị dừng giữa chừng
    #                                                                      cũng không để sót vào data/docs
    if not A.is_self_hosted(cfg["base_url"]):
        A.wait_for = lambda c, max_out: 60.0      # model free treo thì bỏ sau 60 s (app mặc định chờ 15 phút)
    cases = json.loads(pathlib.Path(args.gold).read_text("utf-8"))
    norm = lambda t: A._norm_map(t)[0].strip(" .,;:")  # noqa: E731
    score, bad, times = {"đúng": 0, "một phần": 0, "sai": 0, "không tìm thấy": 0, "lỗi LLM": 0}, [], []
    with TestClient(A.app) as c:
        did = c.post("/api/upload", files={"file": ("x.pdf", _blank(), "application/pdf")}).json()["id"]
        try:
            for i, k in enumerate(cases):
                for wait in (0, 10, 30, 60):        # model free: quá tải / 429 thì chờ rồi hỏi lại
                    time.sleep(wait)
                    A._ALIGN_CACHE.clear()
                    t0 = time.perf_counter()
                    r = c.post(f"/api/{did}/align", json={"cfg": cfg, "src": k["src"], "s": k["s"], "e": k["e"],
                                                          "dst": k["dst"]}).json()
                    if not str(r.get("reason") or "").startswith("LLM lỗi"):
                        break
                times.append(time.perf_counter() - t0)
                spans = [(a, a + len(g)) for g in k["gold"] if (a := k["dst"].find(g)) >= 0]
                if str(r.get("reason") or "").startswith("LLM lỗi"):
                    v = "lỗi LLM"
                elif r.get("s") is None:
                    v = "không tìm thấy"
                elif norm(r["match"]) in {norm(g) for g in k["gold"]}:
                    v = "đúng"
                elif any(r["s"] < b and a < r["e"] for a, b in spans):
                    v = "một phần"
                else:
                    v = "sai"
                score[v] += 1
                if v != "đúng":
                    sel = k["src"][k["s"]:k["e"]]
                    bad.append(f"  #{i:<2} {v:<14} {sel[:34]!r:<38} → {str(r.get('match'))[:44]!r:<48} (đáp án {k['gold'][0][:40]!r})")
        finally:
            c.delete(f"/api/docs/{did}")
    print("\n".join(bad))
    times.sort()
    print(f"\n{args.model}: " + " · ".join(f"{k} {v}" for k, v in score.items()) + f" / {len(cases)}"
          f" · trung vị {times[len(times) // 2]:.1f}s, chậm nhất {times[-1]:.1f}s")


def _blank() -> bytes:
    """PDF một trang trống: endpoint /align chỉ cần một tài liệu có thật (không đọc nội dung)."""
    d = A.pymupdf.open()
    d.new_page()
    return d.tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    ap.add_argument("--gold", help="file đáp án (bench/align_gold.json) → chấm tự động thay vì chọn cụm ngẫu nhiên")
    # mặc định: model free trên OpenRouter (nhanh, không tốn tiền); TUF chỉ là dự phòng (--base-url http://100.64.0.1:8080/v1)
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--model", default="qwen/qwen3.8-27b:free")   # model free tốt nhất đã đo (51/60, ~1 s)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="bench/align.json")
    ap.add_argument("--prompt", help="file prompt thay ALIGN_PROMPT (để so hai bản prompt trên cùng các cụm)")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    if args.prompt:
        A.ALIGN_PROMPT = pathlib.Path(args.prompt).read_text("utf-8")
    cfg = {"base_url": args.base_url, "model": args.model, "api_key": args.api_key}
    if args.gold:
        return run_gold(args, cfg)
    rows = []
    with TestClient(A.app) as c:
        st = c.get(f"/api/{args.doc}/state").json()
        pool = []
        for p in st["done"]:
            src = block_texts(c.get(f"/api/{args.doc}/page/{p}/layer").json())
            dst = block_texts(c.get(f"/api/{args.doc}/page/{p}/layer?kind=translated").json())
            for b in src.keys() & dst.keys():
                if len(src[b]) > 80:
                    pool.append((p, b, src[b], dst[b]))
        rng.shuffle(pool)
        for p, b, en, vi in pool:
            if len(rows) >= args.n:
                break
            back = len(rows) % 3 == 2                        # 1/3 số cụm bôi đen ở bản dịch, tìm ngược sang bản gốc
            a, z = (vi, en) if back else (en, vi)
            A_, B_ = sent_spans(a), sent_spans(z)
            if len(A_) != len(B_) or re.search(r"\d+:\s*(let|if|match|return)\b", en):
                continue                                     # khối mã giả (Algorithm …) không phải câu văn
            k = rng.randrange(len(A_))
            sa, sz = a[A_[k][0]:A_[k][1]].strip(), z[B_[k][0]:B_[k][1]].strip()
            ph = pick_phrase(rng, sa, back)
            if not ph:
                continue
            t0 = time.perf_counter()
            r = c.post(f"/api/{args.doc}/align", json={"cfg": cfg, "src": sa, "s": ph[0], "e": ph[1], "dst": sz}).json()
            dt = time.perf_counter() - t0
            row = {"page": p, "block": b, "dir": "vi→en" if back else "en→vi", "src": sa, "sel": sa[ph[0]:ph[1]],
                   "dst": sz, "match": r.get("match"), "reason": r.get("reason"), "s": round(dt, 2)}
            rows.append(row)
            print(f"\n[{len(rows)}] tr.{p + 1} đoạn {b} · {row['dir']} · {dt:.1f}s")
            print(f"  bôi đen : {row['sel']}")
            print(f"  → tô    : {row['match'] if row['match'] is not None else '(cả câu) ' + str(row['reason'])}")
            print(f"  câu kia : {sz[:220]}")
    ok = [r for r in rows if r["match"] is not None]
    times = sorted(r["s"] for r in rows)
    print(f"\n{len(ok)}/{len(rows)} cụm LLM trả về có thật trong câu bên kia · "
          f"thời gian trung vị {times[len(times) // 2]:.1f}s, chậm nhất {times[-1]:.1f}s")
    pathlib.Path(args.out).parent.mkdir(exist_ok=True)
    pathlib.Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
