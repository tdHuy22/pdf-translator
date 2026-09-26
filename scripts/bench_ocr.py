"""Bench OCR bằng vision LLM (OpenAI-compatible: llama.cpp, Ollama, LM Studio…) — chỉ cần `pymupdf` và `httpx`,
chạy được ngay trên máy có server (không cần cài cả app).

Ý tưởng: lấy PDF có lớp chữ làm ĐÁP ÁN, tạo bản "scan xấu" từ chính nó, cho model đọc lại rồi so từ.

  # 1) tạo dữ liệu thử: bản scan 72 dpi, JPEG nén mạnh
  python3 scripts/bench_ocr.py --make-scan data/attention-is-all-you-need.pdf --out /tmp/scan72.pdf --dpi 72 --jpeg 40

  # 2) đo một model (mặc định: đọc cả trang một lần)
  python3 scripts/bench_ocr.py --scan /tmp/scan72.pdf --truth data/attention-is-all-you-need.pdf --pages 3-6 \
      --base-url http://127.0.0.1:8080/v1 --model 9b-vision

  # 3) đo kiểu app dùng thật: cắt từng vùng chữ rồi đọc từng vùng
  python3 scripts/bench_ocr.py --scan /tmp/scan72.pdf --truth data/attention-is-all-you-need.pdf --pages 3-6 \
      --base-url http://127.0.0.1:8080/v1 --model PaddlePaddle/PaddleOCR-VL-1.6-GGUF:GGUF --mode region

Kết quả in ra: độ khớp từ (%), số giây mỗi trang/vùng, số lần model bị cắt vì chạm max_tokens.
Đối chiếu nhiều cấu hình bằng --tag, kết quả cộng dồn vào --csv để so sau.
"""
import argparse
import base64
import collections
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

# Prompt riêng của từng model OCR (giống app: xem vlm_ocr_prompt trong app.py)
PROMPTS = {"glm-ocr": "Text Recognition:", "paddleocr-vl": "OCR:", "deepseek-ocr": "Free OCR.",
           "unlimited-ocr": "<image>\ndocument parsing."}
GENERIC_PROMPT = ("Transcribe ALL text in this image exactly as written, in reading order. "
                  "Output only the text, no explanations.")


def prompt_for(model: str) -> str:
    m = model.lower()
    return next((p for k, p in PROMPTS.items() if k in m), GENERIC_PROMPT)


def make_scan(src: Path, out: Path, dpi: int, jpeg: int) -> None:
    """Bản "scan": mỗi trang thành ảnh JPEG nén mạnh, không còn lớp chữ."""
    doc, new = pymupdf.open(src), pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        p = new.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=pix.tobytes("jpeg", jpg_quality=jpeg))
    new.save(out, deflate=True)
    print(f"đã tạo {out} ({len(new)} trang, {dpi} dpi, JPEG {jpeg}, {out.stat().st_size // 1024} KB)")


def words(text: str) -> collections.Counter:
    return collections.Counter(w for w in re.findall(r"[^\W_]+", text.lower()) if w)


def match(truth: str, got: str) -> float:
    """Độ khớp từ, không tính thứ tự: số từ đọc đúng / số từ trong đáp án."""
    t, g = words(truth), words(got)
    return sum((t & g).values()) / max(sum(t.values()), 1)


def ask(base_url: str, model: str, api_key: str, png: bytes, prompt: str, max_tokens: int, timeout: float) -> tuple:
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    if re.search(r"ocr", model, re.I):
        body["stop"] = ["```"]
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r)
    choice = res["choices"][0]
    text = re.sub(r"<think>.*?</think>", "", choice["message"]["content"] or "", flags=re.S)
    text = re.sub(r"```[a-z]*", " ", text)
    return text, choice.get("finish_reason"), time.time() - t, (res.get("usage") or {}).get("completion_tokens", 0)


def page_numbers(spec: str, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a) - 1, int(b or a)))
    return [p for p in out if 0 <= p < n]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make-scan", metavar="PDF", help="tạo bản scan từ PDF có lớp chữ rồi thoát")
    ap.add_argument("--out", default="scan.pdf")
    ap.add_argument("--dpi", type=int, default=72)
    ap.add_argument("--jpeg", type=int, default=40)
    ap.add_argument("--scan", help="PDF scan để đọc")
    ap.add_argument("--truth", help="PDF gốc có lớp chữ (đáp án)")
    ap.add_argument("--pages", default="", help="vd. 3-6 hoặc 1,4,9 (mặc định: tất cả)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="9b-vision")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--mode", choices=("page", "region"), default="page",
                    help="page: đọc cả trang một lần; region: cắt từng vùng chữ như app vẫn làm")
    ap.add_argument("--zoom", type=float, default=2.0, help="cả trang: 2.0 = 144 dpi (app dùng mức này)")
    ap.add_argument("--min-chars", type=int, default=40, help="region: bỏ qua vùng ngắn hơn (app cũng bỏ)")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--max-units", type=int, default=0, help="dừng sau N lượt gọi (chạy thử nhanh)")
    ap.add_argument("--tag", default="", help="tên cấu hình đang thử, in kèm kết quả")
    ap.add_argument("--csv", default="", help="cộng dồn kết quả vào file CSV")
    args = ap.parse_args()

    if args.make_scan:
        make_scan(Path(args.make_scan), Path(args.out), args.dpi, args.jpeg)
        return
    if not (args.scan and args.truth):
        ap.error("cần --scan và --truth (hoặc --make-scan)")

    scan, truth = pymupdf.open(args.scan), pymupdf.open(args.truth)
    prompt = prompt_for(args.model)
    scores, secs, gen, cut, units = [], [], [], 0, 0
    for pno in page_numbers(args.pages, len(scan)):
        if args.max_units and units >= args.max_units:
            break
        page, ref = scan[pno], truth[pno]
        jobs = []  # (khung, đáp án)
        if args.mode == "page":
            jobs.append((page.rect, ref.get_text()))
        else:
            for b in ref.get_text("blocks"):
                if b[6] == 0 and len(b[4].strip()) >= args.min_chars:
                    jobs.append((pymupdf.Rect(b[:4]) + (-3, -3, 3, 3), b[4]))
        for rect, want in jobs:
            if args.max_units and units >= args.max_units:
                break
            # vùng nhỏ thì phóng to để mỗi dòng cao ≥ ~40px (app.ocr_vlm làm như vậy)
            zoom = args.zoom if args.mode == "page" else min(4.0, max(150 / 72, 40 / 12))
            png = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
            cap = len(want) * 2 + 1024 if not re.search(r"ocr", args.model, re.I) else len(want) // 2 + 64
            try:
                text, finish, dt, ntok = ask(args.base_url, args.model, args.api_key, png, prompt, cap, args.timeout)
            except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as e:
                print(f"  trang {pno + 1}: lỗi gọi model: {e!r}", file=sys.stderr)
                continue
            units += 1
            cut += finish == "length"
            scores.append(match(want, text))
            secs.append(dt)
            gen.append(ntok)
        print(f"  trang {pno + 1}: khớp {100 * statistics.mean(scores or [0]):.1f}% (cộng dồn), {sum(secs):.0f}s",
              flush=True)

    if not scores:
        print("không đo được gì")
        return
    line = (f"{args.tag or args.model:28} mode={args.mode:6} khớp {100 * statistics.mean(scores):5.1f}% | "
            f"{statistics.mean(secs):6.1f}s/{'trang' if args.mode == 'page' else 'vùng'} | "
            f"{statistics.mean(gen):5.0f} token ra | chạm max_tokens {cut}/{units}")
    print(line)
    if args.csv:
        new = not Path(args.csv).exists()
        with open(args.csv, "a") as f:
            if new:
                f.write("tag,model,mode,match_pct,sec_per_unit,tokens_out,cut,units\n")
            f.write(f"{args.tag},{args.model},{args.mode},{100 * statistics.mean(scores):.1f},"
                    f"{statistics.mean(secs):.1f},{statistics.mean(gen):.0f},{cut},{units}\n")


if __name__ == "__main__":
    main()
