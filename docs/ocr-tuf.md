# Bench model OCR trên server TUF (giao cho AI chạy trên TUF)

Mục tiêu: tìm **lệnh chạy llama-server tốt nhất cho từng model OCR** trên TUF (GTX 1650 Ti 4 GB, Ryzen 7 4800H 8 nhân/16 luồng, 15 GB RAM, Ubuntu 24.04), để VietPaper trên máy Mac gọi sang đọc chữ trong PDF scan.

Tốt nhất = độ khớp chữ cao nhất; trong các cấu hình đạt ngưỡng thì chọn cái nhanh nhất; không OOM, không lặp vô hạn, không bị cắt vì chạm `max_tokens`.

## 1. App gọi OCR như thế nào (hợp đồng, đừng đổi)

App gửi **một vùng chữ (hoặc cả trang) mỗi request** tới `POST {ocr_vlm_base_url}/chat/completions` kiểu OpenAI:

```json
{"model": "<preset>", "temperature": 0, "max_tokens": 123,
 "messages": [{"role": "user", "content": [
   {"type": "text", "text": "<prompt theo model>"},
   {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]}]}
```

| Chi tiết | Giá trị app dùng |
|---|---|
| Prompt | `PaddleOCR-VL` → `OCR:` · `glm-ocr` → `Text Recognition:` · `deepseek-ocr` → `Free OCR.` · `Unlimited-OCR` → `<image>\ndocument parsing.` (cả trang) · còn lại → prompt chung "Transcribe ALL text…" |
| `max_tokens` | tên model có "ocr": `len(chữ gốc)//2 + 64` kèm `stop: ["```"]`; model khác: `len(chữ gốc)*2 + 1024` |
| Ảnh gửi | vùng chữ + lề 3pt, phóng to `min(4.0, max(150/72, 40/(cỡ chữ/0.8)))` để mỗi dòng cao ≥ ~40px; cả trang thì 144 dpi (zoom 2.0), PNG |
| Số request song song | `ocr_vlm_concurrency` — với TUF để **1** (`--parallel 1`) |
| Khi `finish_reason == "length"` | app **bỏ** kết quả của model, giữ chữ RapidOCR → cấu hình nào hay chạm `max_tokens` coi như hỏng |
| Sau khi nhận | app bỏ `<think>…</think>`, bỏ ```` ``` ````, gộp khoảng trắng, bỏ câu lặp, chuyển LaTeX đơn giản thành chữ |

Ngoài ra app còn nhận **endpoint OCR riêng** (không phải LLM): `POST /ocr` multipart `file=<ảnh png>`, `lang=<mã>` → `{"lines": [{"bbox": [x0,y0,x1,y1], "text": "..."}]}` theo pixel ảnh. Nếu muốn chạy hẳn PaddleOCR/RapidOCR trên TUF thì theo mẫu `examples/custom_ocr_server.py`.

Cấu hình phía Mac khi dùng TUF: `ocr_engine="vlm"`, `ocr_vlm_base_url="http://100.64.0.1:8080/v1"`, `ocr_vlm_model="<preset>"`, `ocr_vlm_concurrency=1`.

## 2. Chuẩn bị trên TUF

```bash
pip install --user pymupdf          # script chỉ cần pymupdf (dùng urllib, không cần httpx)
# chép scripts/bench_ocr.py, samples/attention.pdf từ repo VietPaper sang, rồi:
python3 bench_ocr.py --make-scan attention.pdf --out scan72.pdf --dpi 72 --jpeg 40
python3 bench_ocr.py --make-scan attention.pdf --out scan60.pdf --dpi 60 --jpeg 35
```

PDF gốc có lớp chữ chính là **đáp án**; bản scan là ảnh nén xấu của chính nó, không còn lớp chữ.

## 3. Đo một cấu hình

```bash
# đọc cả trang một lần (nhanh, hợp với model tự phân tích bố cục)
python3 bench_ocr.py --scan scan72.pdf --truth attention.pdf --pages 3-6 \
    --base-url http://127.0.0.1:8080/v1 --model 9b-vision --tag "9b-vision ngl=8" --csv ocr.csv

# đọc từng vùng chữ, đúng như app dùng thật
python3 bench_ocr.py --scan scan72.pdf --truth attention.pdf --pages 3-6 --mode region \
    --base-url http://127.0.0.1:8080/v1 --model PaddlePaddle/PaddleOCR-VL-1.6-GGUF:GGUF --tag "paddle mặc định" --csv ocr.csv
```

Script in ra: độ khớp từ (%), giây mỗi trang/vùng, số token sinh ra, số lần chạm `max_tokens`; `--csv` cộng dồn để so sau.

Lưu ý khi đo:
- Lần gọi đầu tiên gồm cả thời gian nạp model — đo lần thứ hai mới là tốc độ thật. Ghi cả hai.
- Đo VRAM đỉnh song song: `nvidia-smi --query-gpu=memory.used --format=csv -l 1`.
- Mỗi lần đổi preset: `systemctl --user restart llm-router` rồi chờ `curl -s localhost:8080/health`.

## 4. Tham số nên quét và lý do

| Tham số | Gợi ý thử | Vì sao |
|---|---|---|
| `--n-gpu-layers` | 0, 8, 16, 20, 99 | VRAM chỉ 4 GB; model vision còn tốn thêm cho mmproj. Preset hiện tại: `9b-vision` ngl=8, `4b-vision` ngl=20, `2b-vision` ngl=99 |
| `--no-mmproj-offload` | có/không | để bộ mã hoá ảnh chạy CPU, nhường VRAM cho các lớp model |
| `--ctx-size` | 4096, 8192, 16384 | ảnh chiếm hàng nghìn token; đọc từng vùng thì 4096–8192 là đủ, đọc cả trang cần nhiều hơn. Ctx càng lớn KV càng tốn VRAM |
| `--cache-type-k/v` | q8_0, q4_0, f16 | giảm VRAM cho KV; q4_0 có thể làm chữ sai hơn — phải đo |
| `--flash-attn` | on/off | thường nhanh hơn và đỡ VRAM trên Turing |
| `--batch-size` / `--ubatch-size` | 512/256, 1024/512, 2048/512 | quyết định tốc độ nạp ảnh (phần lớn thời gian OCR nằm ở đây), nhưng ubatch lớn tốn VRAM |
| `--threads` | 8 (thử 16) | 8 nhân thật; SMT thường không nhanh hơn |
| `--parallel` | 1 | app gửi tuần tự từng vùng |
| `--temp 0`, `--top-k 1` | luôn | OCR cần kết quả xác định |
| `--repeat-penalty` | 1.0, 1.05, 1.1 | GLM-OCR hay lặp câu tới hết `max_tokens` |
| `--sleep-idle-seconds` | 1800 | router tự nhả VRAM khi không dùng |

Model nên thử: `9b-vision`, `4b-vision`, `2b-vision` (đã có sẵn trong models.ini), `PaddlePaddle/PaddleOCR-VL-1.6-GGUF:GGUF`, `ggml-org/GLM-OCR-GGUF:Q8_0`, và Unlimited-OCR (`hf.co/sahilchachra/Unlimited-OCR-GGUF:Q4_K_M`, chỉ chạy được kiểu cả trang).

## 5. Mốc đã đo sẵn (để biết thế nào là đạt)

Đo trên máy Mac M2 16 GB, 18–19/09/2026, trang scan xấu 72 / 60 dpi, chấm bằng độ khớp từ:

| Engine | 72 dpi | 60 dpi | Thời gian |
|---|---|---|---|
| RapidOCR (không dùng LLM) | 88,8% | 60,6% | 1–3 giây/trang |
| qwen3.5:latest (Ollama, vision) | 99,9% | 93,1% | 15–70 giây/trang |
| glm-ocr | 99,8% | 84,9% | 30–45 giây |
| PaddleOCR-VL-1.6 | 98,4% | — | nhanh hơn GLM-OCR 2–3 lần |
| TUF `9b-vision` (ngl=8) | 100% | 93,0% | ~28 giây |
| TUF `2b-vision` (ngl=99) | 99,3% | 85,7% | 50–70 giây (vẫn "suy nghĩ" dù đã tắt) |

Đo thêm trên chính TUF ngày 20/09/2026 bằng `bench_ocr.py` (scan 72 dpi tạo từ `samples/attention.pdf`, preset `9b-vision` đang có, ngl=8):

| Cách đọc | Độ khớp | Thời gian |
|---|---:|---|
| `--mode region` (từng vùng chữ, ảnh phóng 4×) | 99,7% (3 vùng) | **147 giây/vùng** |
| `--mode page` (cả trang 144 dpi) | — | **> 25 phút/trang, phải dừng giữa chừng** |

→ Độ chính xác đã rất tốt, **vấn đề là tốc độ**: một trang scan ~10 vùng sẽ mất khoảng 25 phút. Đó là việc cần giải: `9b-vision` chỉ đẩy được 8 lớp lên GPU 4 GB.

Ngưỡng đề xuất: **≥ 95% ở 72 dpi**, không lần nào chạm `max_tokens`, và **≤ 20–30 giây/vùng** (để một trang scan xong trong ~5 phút). Trong số đạt thì chọn cấu hình nhanh nhất; nếu không preset nào đạt, báo lại để app dùng RapidOCR (1–3 giây/trang, 88,8%) hoặc PaddleOCR-VL chạy trên máy Mac.

## 6. Cần báo cáo lại

1. File `ocr.csv` đầy đủ các lần đo.
2. Với mỗi model: preset tốt nhất, dán nguyên khối `models.ini`, kèm VRAM đỉnh và giây/trang (lần nạp đầu và lần sau).
3. Ghi chú lỗi gặp phải: OOM, lặp câu, chạm `max_tokens`, model không nhận ảnh, prompt nào bắt buộc.
4. Khuyến nghị cuối: dùng model nào cho scan thường và scan xấu (60 dpi), và có nên dùng `--mode region` hay đọc cả trang.

Đừng sửa code app trên Mac; chỉ đổi `~/.config/llama/models.ini` và các tham số chạy. Nếu thấy hợp đồng ở mục 1 cản trở (ví dụ model cần prompt khác), ghi vào báo cáo thay vì tự đổi.
