"""Tạo resources/english_zipf.tsv: tần suất các từ tiếng Anh thông dụng (thang Zipf >= 3.0) để app chấm độ "lạ" của
ứng viên thuật ngữ (dataset/size/training là từ thường; softmax/uptraining/encoder thì không).
Chỉ cần chạy lại khi muốn đổi ngưỡng. wordfreq nặng ~58 MB nên không phải thư viện của app — cài vào venv riêng:

    python3 -m venv /tmp/wf && /tmp/wf/bin/pip install wordfreq
    /tmp/wf/bin/python scripts/make_english_zipf.py
"""
import re
from importlib.metadata import version
from pathlib import Path

from wordfreq import top_n_list, zipf_frequency

MIN_ZIPF = 3.0   # ~1 lần mỗi triệu từ; hiếm hơn nữa thì coi là từ chuyên ngành
OUT = Path(__file__).resolve().parent.parent / "resources" / "english_zipf.tsv"

rows = []
for w in top_n_list("en", 200_000):
    if not re.fullmatch(r"[a-z][a-z'-]*", w):
        continue
    z = zipf_frequency(w, "en")
    if z < MIN_ZIPF:
        break
    rows.append(f"{w}\t{z:.1f}")
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(f"# Từ tiếng Anh thông dụng, thang Zipf (log10 số lần mỗi tỉ từ) >= {MIN_ZIPF}, từ wordfreq "
               f"{version('wordfreq')} (dữ liệu CC BY-SA 4.0). Tạo bằng scripts/make_english_zipf.py\n"
               + "\n".join(rows) + "\n")
print(f"{len(rows)} từ → {OUT}")
