"""Kiểm phần hậu xử lý của OCR bằng vision LLM, KHÔNG cần server LLM:
- latex_to_text: công thức trong dòng mà model OCR chép ra LaTeX (Xiaomi-OCR-0) → chữ thường để dịch/in lại;
- mask_others: ảnh cắt gửi VLM không còn dòng của đoạn bên cạnh, dòng của chính đoạn còn nguyên;
- prompt theo tên model (vùng chữ, công thức).

    .venv/bin/python scripts/test_ocr_vlm.py      # vài giây

In ✓/✗ cho từng mục, mã thoát 1 nếu có mục trượt."""
import pathlib
import sys

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as A

FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


print("1. LaTeX trong dòng → chữ")
# đầu ra thật của Xiaomi-OCR-0 trên FlashAttention-2 (scan 100 dpi, 02/11/2026)
CASES = [
    (r"Given \(\mathbf{Q},\mathbf{K},\mathbf{V}\in \mathbb{R}^{N\times d}\) where \(N\) is", "Given Q,K,V∈ RN× d where N is"),
    (r"which takes \( O(N^{2}) \) memory", "which takes O(N²) memory"),
    (r"Often \( N \gg d \)", "Often N ≫ d"),
    (r"\( ds = (\text{diag}(p) - pp^{\top})dp \)", "ds = (diag(p) - pp⊤)dp"),
    (r"around \(2\times\) speedup", "around 2× speedup"),
    (r"\(\mathbf{S}^{(1)}, \mathbf{S}^{(2)} \in \mathbb{R}^{B_r \times B_c}\)", "S⁽¹⁾, S⁽²⁾ ∈ RBᵣ × Bc"),
    (r"scaling by \(1/\sqrt{d}\)", "scaling by 1/√d"),
    (r"\(\alpha_{t}\leq 0.5\)", "αₜ≤ 0.5"),
    (r"\(\frac{a+b}{2}\)", "(a+b)/2"),
    (r"value $x_i$ here", "value xᵢ here"),
    (r"\(a = b \\ c = d\)", "a = b c = d"),                         # xuống dòng trong công thức, không để lại "\"
    (r"costs $5 and $10 each", "costs $5 and $10 each"),          # dấu $ của tiền, không phải công thức
    (r"the \( ^{*} \) mark", "the * mark"),                        # kiểu chỉ số của Unlimited-OCR (đã có từ trước)
    ("## 2.3 FLASHATTENTION", "2.3 FLASHATTENTION"),             # tiêu đề Markdown
    ("**Abstract** text", "Abstract text"),
    # review 03/11/2026: "\" lẻ, ngoặc thoát, ** của code, # không phải tiêu đề, dòng trống giữ nguyên
    (r"\(a\\)", "a"),
    ("\\(a\\\nb\\)", "a b"),
    (r"\(a^{\}}\)", "a}"),
    (r"x $y\$ z", r"x $y\$ z"),
    ("compute x**2 + y**2 and f(**kw)", "compute x**2 + y**2 and f(**kw)"),
    ("a\n\n# Intro", "a\n\nIntro"),
    ("# of samples", "# of samples"),
]
for src, want in CASES:
    got = A.latex_to_text(src)
    ok(got == want, f"{src!r} → {got!r}" + ("" if got == want else f" (cần {want!r})"))

print("2. Che dòng của đoạn bên cạnh trong ảnh cắt")
pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 400, 100), False)
pix.clear_with(0)  # toàn đen = toàn "chữ"
clip, zoom = pymupdf.Rect(0, 0, 200, 50), 2.0
own = [pymupdf.Rect(10, 20, 190, 32), pymupdf.Rect(10, 32, 190, 44)]
others = [pymupdf.Rect(10, 8, 190, 22),     # dòng cuối đoạn trước, khung chồng 2pt lên dòng đầu của đoạn này
          pymupdf.Rect(10, 46, 190, 60)]    # dòng đầu đoạn sau, lòi ra ngoài khung cắt
A.mask_others(pix, clip, zoom, own, others)
px = lambda x, y: pix.pixel(int(x * zoom), int(y * zoom))[0]
ok(px(100, 12) == 255, "dòng đoạn trước bị tô trắng")
ok(px(100, 48) == 255, "dòng đoạn sau bị tô trắng")
ok(px(100, 26) == 0 and px(100, 38) == 0, "lõi hai dòng của đoạn còn nguyên")
ok(px(100, 21) == 255, "phần khung đoạn trước chồng lên mép trên dòng đầu (ngoài lõi) vẫn được tô")
ok(px(5, 26) == 0, "ngoài khung dòng khác thì không đụng")

print("3. Prompt theo model")
ok(A.vlm_ocr_prompt("xiaomi-ocr") == "Extract the text in the image.", "Xiaomi-OCR-0: prompt vùng chữ chính thức")
ok(A.vlm_ocr_prompt("longwayxu/xiaomi-ocr-0:bf16") == "Extract the text in the image.", "tên model Ollama cũng nhận ra")
ok(A.formula_prompt("xiaomi-ocr").startswith("Identify the formula"), "Xiaomi-OCR-0: prompt công thức chính thức")
ok(A.vlm_ocr_prompt("paddleocr-vl") == "OCR:", "PaddleOCR-VL giữ prompt cũ")

print("\nTẤT CẢ ĐỀU ĐẠT" if not FAIL else f"\n{len(FAIL)} mục trượt")
sys.exit(1 if FAIL else 0)
