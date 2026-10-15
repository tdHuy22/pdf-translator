# VietPaper – Dịch bài báo PDF → Tiếng Việt (song song, giữ bố cục)

## Cài đặt & chạy
Yêu cầu: macOS/Linux, Python ≥ 3.10.
```
./run.sh          # lần đầu tự tạo .venv và cài thư viện, sau đó mở http://127.0.0.1:8000
```
App có **đăng nhập** (một mật khẩu): đặt một lần trên máy chạy app trước khi mở, chưa đặt thì mọi request bị từ chối (503).
```
.venv/bin/python auth.py --set-password     # đặt / đổi mật khẩu (đổi là đăng xuất mọi nơi)
```
Chạy nền và bật/tắt bằng `restart.sh` (log ghi vào `app.log`):
```
./restart.sh            # khởi động lại (sau khi sửa code thì chạy lệnh này)
./restart.sh start|stop|status|logs
```
Thử nhanh: 10 bài bench trong `data/` (PDF có lớp chữ) và bản scan của chúng trong `samples/<tên>-scan.pdf` (không nằm trong git, tạo bằng `.venv/bin/python scripts/make_scans.py`: 200 dpi, ảnh xám, lệch trang ±0,6°, nhiễu hạt, JPEG q=60, seed cố định).

Mã nguồn: `app.py` (toàn bộ phía server), `auth.py` (đăng nhập, chặn request lạ, header bảo mật), `store.py` (lưu tài liệu xuống đĩa), `notes.py` (ghi chú), `chats.py` (phiên trò chuyện), `static/index.html` (bố cục + CSS), `static/app.js` (giao diện), `static/render.js` (Markdown + công thức + code cho câu trả lời LLM và ghi chú), `static/notes.js` (ghi chú), `static/view.js` (hai khung xem, thu phóng, chế độ xem, kính lúp, bề rộng khung chat), `static/login.html` + `login.js` (trang đăng nhập), `static/theme.js` (nút sáng/tối). Không có script nội tuyến hay `onclick="…"` trong HTML: CSP của app chặn chúng. File tĩnh được trả kèm `Cache-Control: no-cache` nên sửa xong tải lại trang là thấy ngay.

## LLM hỗ trợ (mọi API tương thích OpenAI)
Có sẵn (API cloud, cần API key; không sửa/xoá được):

| Nhà cung cấp | Base URL | Model mặc định | Ghi chú |
|---|---|---|---|
| OpenRouter | https://openrouter.ai/api/v1 | `inclusionai/ling-3.1-flash` (dịch, trò chuyện), `dots-studio/dots-3-note-preview:free` (ảnh/OCR) | **mặc định** của app, **free** (03/10/2026). ling-3.1-flash: bench_english **0,61** từ tiếng Anh sót/100 từ (tốt nhất đã đo), 74 s cho 6 trang, không đoạn nào bị bộ lọc loại; bench trò chuyện 30/30, ~6,7 s/câu; bảng thuật ngữ dev 90,0 / 67,4%, test 84,1 / 76,5%; không đọc ảnh nên phần ảnh dùng dots. **Tên không có đuôi `:free`**: OpenRouter đang để giá 0, nếu đổi giá thì request vẫn chạy và tính vào tài khoản (model `:free` thì báo lỗi). Trước đó: `inclusionai/ling-3.0-flash-fin:free` (26/09, 0,87/100, nhanh gấp 3 dots) hết free ngày 03/10/2026, `google/gemini-2.5-flash` trả phí — cài đặt trình duyệt còn để mặc định cũ tự chuyển. Đã thử cùng ngày: `inclusionai/ling-3.0-flash-sante:free` (1,41/100, 72 s; trò chuyện 30/30 nhanh gấp đôi, ~3,3 s/câu) và `stealth/space-bunny-alpha` (1,29/100, 113 s; OpenRouter ghi hạn dùng 05/10/2026) — model **bắt buộc suy nghĩ** (từ chối `reasoning: {enabled: false}`) → app xin mức thấp nhất `effort: minimal`: bỏ hẳn tham số thì model nghĩ hết mức, phần nghĩ ăn hết trần token, một trang BERT chỉ 5/14 đoạn được dịch (minimal: 14/14, nhanh gấp 3) |
| OpenAI | https://api.openai.com/v1 | `gpt-4.1-mini` | |
| Claude (Anthropic) | https://api.anthropic.com/v1 | `claude-opus-5` | lớp tương thích OpenAI của Anthropic: đọc ảnh được; bỏ qua `response_format` và file PDF (📎 Nguyên PDF tự lùi về chữ) |
| DeepSeek | https://api.deepseek.com/v1 | `deepseek-flash` | `deepseek-flash` đọc được ảnh, `deepseek-v4-pro` thì không |

**Nhà cung cấp của bạn** (gieo sẵn lần đầu chạy, sửa/xoá được): Ollama local (`http://localhost:11434/v1`), server TUF (llama.cpp, mặc định cho OCR Vision LLM nếu còn), proxy ChatGPT. Preset llama.cpp / LM Studio local đã bỏ (26/09/2026) — cài đặt cũ còn chọn chúng tự chuyển sang OpenRouter; server kiểu này vẫn dùng được qua “Tùy chỉnh” hoặc lưu thành nhà cung cấp của bạn.

Server khác (vLLM, llama.cpp, LM Studio, proxy riêng…) thì lưu thành **nhà cung cấp của bạn**: ⚙️ Cấu hình → mục 7 (tên, Base URL, key, model mặc định cho dịch / OCR / thuật ngữ), hoặc nút 💾 cạnh mỗi ô “Nhà cung cấp” để lưu nhanh Base URL + model đang nhập. Lưu trên server trong `data/providers.json` (không chứa key — key vẫn lưu theo Base URL trong `data/keys.json`); lần đầu chạy app gieo sẵn Ollama local, server TUF và proxy ChatGPT vào đây (bản cài cũ: Ollama được chèn thêm một lần, đánh dấu bằng `data/.providers_v2`). API: `GET/POST /api/providers`, `DELETE /api/providers/<id>`.
Ô **Model** là ô gõ-để-lọc: bấm vào là hiện danh sách model của server (nhớ 24 giờ, ↻ để tải lại), gõ vài từ để lọc (`qwen 27` khớp `qwen/qwen3.8-27b:free`), ↑↓ Enter để chọn; model của OpenRouter có nhãn `free`, `👁 ảnh` (đọc được ảnh) và độ dài ngữ cảnh.

## OCR (PDF scan)
| Engine | Loại | Ghi chú |
|---|---|---|
| **RapidOCR** (mặc định) | local, miễn phí | Model PP-OCR của PaddleOCR chạy bằng ONNX, tự tải ~15MB lần đầu. Ngôn ngữ: `eng`, `ch`, `latin`, `japan`, `korean`… Dòng chữ được gom thành đoạn theo vùng của **PP-DocLayoutV3** (ONNX, tự tải ~130MB vào `models/layout/` lần đầu) bằng **đúng bộ gom đoạn của PDF có lớp chữ**: đúng đoạn, đúng thứ tự đọc, bỏ qua hình/công thức/chữ dọc bên lề, bảng dịch từng ô, thuật toán từng dòng (xem *Bench pipeline OCR*) |
| Tesseract | local, miễn phí | `brew install tesseract tesseract-lang`, ngôn ngữ `eng`, `eng+vie`… Dòng chữ cũng được gom theo PP-DocLayout |
| OCR.space | API miễn phí | Đăng ký key miễn phí tại ocr.space (key demo `helloworld` bị giới hạn) |
| Vision LLM | local hoặc API | Mọi API OpenAI-compatible có vision: Ollama, llama.cpp, LM Studio, OpenRouter… PP-DocLayout + RapidOCR định vị đoạn, VLM đọc lại chữ từng vùng → tốt với scan xấu. Prompt tự chọn theo tên model: `PaddleOCR-VL` → `OCR:`, `glm-ocr` → `Text Recognition:`. Riêng **Unlimited-OCR** gửi cả trang (150 dpi) một lần; PP-DocLayout làm "trọng tài": bỏ dòng không khớp vùng nào, vùng bị sót thì đọc bù bằng RapidOCR |
| Endpoint tuỳ chỉnh | local hoặc API | Server OCR riêng (PaddleOCR, Surya, docTR…). Xem `examples/custom_ocr_server.py` |

**Hợp đồng endpoint tuỳ chỉnh:** `POST` multipart `file=<page.png>`, `lang=<mã>` (header `Authorization: Bearer <key>` nếu có) → trả `{"lines": [{"bbox": [x0,y0,x1,y1], "text": "..."}]}` theo pixel ảnh (hoặc `"box": [[x,y]×4]` kiểu PaddleOCR).

**Số vùng gửi cùng lúc** (Vision LLM): Ollama / API cloud để 4; llama-server chạy `-np 1` (vd. server TUF) để 1 — gửi nhiều hơn chỉ phải xếp hàng và dễ quá thời gian chờ. Chọn "Nhà cung cấp" sẽ tự điền giá trị hợp lý. Con số này nhân với "Số trang dịch song song".

**Model layout PP-DocLayoutV3** được ghim đúng commit và kiểm tra SHA-256 khi tải; tải lỗi (mất mạng) thì app gộp dòng theo khoảng cách như cũ và 10 phút sau mới thử tải lại.

**Model có chế độ suy nghĩ** (Qwen3/3.5, DeepSeek-R1, phần lớn model free trên OpenRouter…): giữ bật "Tắt thinking", nếu không mỗi trang có thể mất hàng chục phút. Server tự host nhận `reasoning_effort: "none"` + `chat_template_kwargs.enable_thinking=false`; OpenRouter nhận `reasoning: {enabled: false}` — áp dụng cho mọi lượt gọi (dịch, thuật ngữ, trò chuyện, căn vệt tô). Trước 25/09/2026 chỉ lượt lập thuật ngữ và căn vệt tô tắt được trên OpenRouter: dịch một trang bằng Nemotron Ultra free chỉ ra 4/12 đoạn sau 13 lượt gọi (phần nghĩ ăn hết trần token); sau khi sửa: 12/12 đoạn, 1 lượt gọi, 40 giây.

**Tham số bị từ chối**: model nào báo lỗi vì `temperature` (model suy nghĩ của OpenAI, vd. `gpt-6-luna` qua proxy ChatGPT) hay vì không cho tắt suy nghĩ thì app bỏ đúng tham số đó, hỏi lại một lần, và nhớ cho các lượt sau — chỉ khi lỗi có nhắc tới tham số đó và lần hỏi lại thành công. Lỗi khác (vượt ngữ cảnh, 429, sai key, mất kết nối) không làm mất `temperature` của các lượt dịch sau.

Chế độ OCR: **Tự động** (OCR trang không có lớp chữ, **hoặc lớp chữ đọc ra rác** — PDF cũ có font thiếu bảng mã, trên 25% ký tự là ký tự thay thế/vùng dùng riêng), **Luôn OCR**, **Tắt**.

## Cách app gửi request cho model
Đo trên qwen3.5 (19/09/2026): tiếng Anh ~4,7 ký tự/token, tiếng Việt ~3,5, chữ Hán ~1,7; prompt hệ thống ~370 token. Prompt dài hơn ngữ cảnh thì **Ollama cắt âm thầm** (gửi ~5.500 token, model chỉ nhận 2.050). Vì vậy app **đo tài liệu → hỏi server → chọn cách gửi**:

**Hỏi server một lần cho mỗi (URL, model)** — in ra log khi dịch trang đầu:
| Server | Ngữ cảnh lấy ở đâu | Số lượt song song |
|---|---|---|
| Ollama | ngữ cảnh tối đa của model (`/api/show`), app tự đặt `num_ctx` qua `/api/chat` | 1 |
| llama.cpp | `n_ctx` ở `/props`; chế độ router (tự nạp model, như server TUF) trả 0 → lấy `--ctx-size` của preset trong `/v1/models` | `total_slots` / `--parallel` |
| OpenRouter, OpenAI, LM Studio… | `context_length` của model trong `/v1/models` nếu server có trả, không thì 32768 | 4 |

**Chọn cách gửi:**
- `num_ctx` (chỉ Ollama): bậc nhỏ nhất đủ cho trang nặng nhất của tài liệu, không vượt ngữ cảnh tối đa của model; cố định cho cả tài liệu vì đổi giữa chừng là Ollama nạp lại model (~8 giây). Đặt tay bằng ô `num_ctx` nếu muốn. Thêm ngữ cảnh gần như không tốn RAM với qwen3.5 (4096 → 16384: 5,06 → 5,38 GB).
- Lô = **cả trang nếu vừa 85% ngữ cảnh**, tối đa 40 đoạn; nếu không thì cắt theo token ước lượng (chữ gốc + bản dịch dự kiến + khung JSON mỗi đoạn). Đoạn trùng chữ (nhãn hàng lặp lại trong bảng) chỉ dịch một lần — trang 155 ô của DataSentinel còn 16 câu.
- **Gộp 2–3 trang vào một lượt gọi khi ngữ cảnh còn trống** (`Gộp trang mỗi lượt gọi`, mặc định *Tự động*, tối đa 3): một nhóm = đúng **một** lượt gọi, trang sau chỉ được thêm khi cả nhóm vẫn nằm trong 85% ngữ cảnh **và** trong 40 đoạn. Đo trên `bert.pdf`: ngữ cảnh 4096 → 1 trang/lượt, 8192 → 2 trang, 16384 → 3 trang. Được gì: câu bị ngắt sang trang sau được dịch liền mạch trong cùng một lượt (không phải gửi kèm câu "prev"/"next" nữa), đầu bảng lặp lại ở trang sau chỉ dịch một lần, và prompt hệ thống chỉ đọc một lần cho cả nhóm. Trang phải OCR bằng Vision LLM/OCR.space/endpoint riêng thì dừng nhóm ở đó (30–70 giây/trang, gộp vào là phải chờ cả nhóm mới thấy kết quả); RapidOCR chỉ ~3 giây/trang nên trang scan vẫn được gộp như trang có lớp chữ.
- **Trần token đầu ra** cho mỗi lượt (`num_predict` / `max_tokens`) ≈ 1,6 × độ dài dự kiến: model nhỏ không lặp vô hạn, API tính tiền không sinh tràn.
- **Không gửi quá số slot** của server: llama.cpp/Ollama một slot thì request thứ hai chỉ nằm xếp hàng.
- **Phần đầu prompt giữ nguyên cho cả tài liệu** (prompt hệ thống + tên bài + bảng thuật ngữ) để server tái dùng KV cache.
- **Phát hiện prompt bị cắt**: server báo nhận ít hơn 80% số token đã gửi → tự chia đôi lô rồi gửi lại.
- Với Ollama còn ép đầu ra đúng khung JSON (`format` = JSON schema) nên không còn cảnh JSON hỏng phải dịch lại từng đoạn; đo được là không làm chậm.
- Số token thật của từng trang hiện khi di chuột lên ảnh trang dịch.

## Thuật ngữ & độ dài
- **Bảng thuật ngữ tự lập** (bật sẵn): trước khi dịch trang đầu, app rút các thuật ngữ của bài rồi hỏi LLM **một lượt** để chốt cách dịch từng thuật ngữ — hoặc giữ nguyên tiếng Anh nếu đó là thói quen học thuật. Bảng này nằm trong phần đầu prompt của mọi lượt gọi, nên cả bài dùng thuật ngữ như nhau. Bảng bạn tự nhập luôn được ưu tiên.
  - **Rút thuật ngữ** (`term_candidates`, không gọi LLM, ~0,2–0,5 giây/bài): đọc thân bài (bỏ tên tác giả trước Abstract, tài liệu tham khảo — phụ lục sau đó vẫn tính, chữ trong hình/bảng, ký hiệu toán), rồi lấy: chữ viết tắt bài tự định nghĩa kèm dạng đầy đủ (`Rotary Position Embedding (RoPE)`, thuật toán Schwartz–Hearst), tên riêng/ký hiệu (`GQA`, `FlashAttention-2`, `A100`), cụm 2–3 từ lặp ≥ 3 lần (`multi-head attention`, `target data`; bỏ mảnh của cụm dài hơn như `scaled dot-product`), và từ đơn không phổ thông. Gộp số ít/số nhiều và hoa/thường; bỏ tên người (đứng trước *et al*).
  - **PDF scan / PDF có trang scan** (trang không có lớp chữ hoặc lớp chữ rác): chữ để rút thuật ngữ của các trang đó lấy từ **khối OCR**, trang có chữ vẫn đọc lớp chữ (`scan_text`, `paper_text(doc, ocr)`): thân bài = khối văn xuôi sau Abstract, tiêu đề mục = nhãn `paragraph_title`, tài liệu tham khảo = nhãn `reference_content`; tên bài = khối `doc_title` của trang đầu (`blocks_title`). Trước khi lập bảng app OCR trước các trang scan, gặp tiêu đề *References* thì OCR thêm một trang rồi dừng; nhiều hơn 30 trang scan (sách) thì 10 trang đầu + 20 trang rải đều tới cuối. Ô bảng thuật ngữ hiện tiến độ (`đang OCR trang scan 3/9…`, ~3–5 giây/trang trên M2; OCR hai trang song song lại chậm hơn: 7,4 so với 4,6 giây/trang). Với RapidOCR, khối được lưu nên lúc dịch các trang đó không phải OCR lại; Vision LLM/OCR.space/endpoint riêng (30–70 giây/trang) thì bảng đọc tạm bằng RapidOCR (khối tạm chỉ giữ trong bộ nhớ, không thay khối dùng để dịch; phần trò chuyện cũng đọc được chữ này cho trang chưa dịch).
  - **LLM xếp loại và dịch** (`build_glossary`, một lượt): 40 ứng viên đầu, mỗi cái kèm một câu của bài chứa nó, cùng tên bài và đầu abstract. Với từng ứng viên LLM trả `kind` — `name` (tên riêng/viết tắt, giữ tiếng Anh), `term` (khái niệm, kèm cách dịch), `other` (không phải thuật ngữ, bỏ) — theo schema JSON ép bằng llama.cpp/Ollama. Loại phải là trường riêng: khi chỉ có một trường "text", model `4b` lẫn "giữ ứng viên" với "giữ nguyên tiếng Anh" và chép lại nguyên văn mọi ứng viên. Có câu ngữ cảnh thì dịch đúng nghĩa trong bài hơn (`position embedding` → "nhúng vị trí" thay vì "đóng gói vị trí", `dot products` → "tích vô hướng" thay vì "sản phẩm tích vô hướng").
  - **Kiểm lại câu trả lời của LLM** (`gloss_rows`): tên riêng/viết tắt luôn giữ nguyên dù LLM lỡ dịch, dạng đầy đủ ghi kèm viết tắt (`nhúng vị trí xoay (RoPE)`), bỏ lời giải thích trong ngoặc, bản dịch dài bất thường và bản "dịch" thành một thuật ngữ tiếng Anh khác. Hai thuật ngữ khác nhau bị dịch cùng một chữ thì hàng sau hiện trên bảng nhưng **bỏ tích Dùng** — để nguyên thì bảng sẽ xoá mất khác biệt giữa hai khái niệm. Ứng viên LLM xếp là thuật ngữ mà bỏ trống cách dịch, hay xếp là tên riêng mà toàn chữ thường (`high bandwidth memory`, `causal mask` — lỗi của `4b` trên FlashAttention-2 bản scan) thì hỏi lại riêng cách dịch của chúng trong một lượt nhỏ (`GLOSSARY_FILL_PROMPT`).
  - **Gợi ý khác & học từ bạn**: dưới bảng là các chip *Gợi ý khác* — ứng viên LLM không chọn và ứng viên hạng 41–80 (không gửi LLM) — bấm để thêm vào bảng (mặc định giữ nguyên tiếng Anh). Mỗi hàng có nút ✕ để xoá. Khi bấm **Lưu bảng**, app so với bảng trước đó và nhớ vào `data/user_terms.json` cho **mọi tài liệu sau**: thuật ngữ bị xoá thì không gợi ý nữa (hiện ở *Đang ẩn vì bạn từng xoá*, bấm ↺ để bỏ ẩn); thuật ngữ bạn sửa cách dịch, đổi dịch↔giữ nguyên hay tự thêm thì được dùng đúng cách đó, và được thêm vào bảng của bài nào có thuật ngữ ấy (≥ 2 lần) dù LLM không chọn. Bỏ tích *Dùng* chỉ có tác dụng trong bài đó. Bench không dùng file này (đo thuật toán, không đo sở thích của bạn).
  - **Model lập bảng riêng** (Cài đặt → *4. Lập bảng thuật ngữ*, mặc định dùng đúng model dịch): chỉ một lượt gọi cho mỗi tài liệu, nên có thể dùng model cloud, vd. OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free`. Với model suy luận trên OpenRouter app tắt phần suy nghĩ cho lượt này (225 → 13 giây, lọc/dịch vẫn tốt). OpenRouter hay trả "quá tải" với model free → thử lại một lần, rồi lùi về model dịch. Muốn dùng model free phải cho phép trong https://openrouter.ai/settings/privacy (bỏ yêu cầu ZDR) và guardrail của workspace — tức chấp nhận nhà cung cấp có thể lưu/dùng nội dung gửi đi.
  - **Đo** bằng `scripts/bench_glossary.py` trên 6 bài có đáp án tay (`bench/glossary_gold.json`; 4 bài dev để chỉnh luật, 2 bài test giữ riêng; `--rescore` chấm lại mọi lượt đã lưu theo đáp án hiện tại). Chỉ phần code, 40 thuật ngữ đầu, so với cách cũ (regex cụm viết hoa + đếm từ): độ chính xác dev 55 → 85%, test 51 → 80%; độ phủ dev 34 → 74%, test 26 → 81%. Cả bảng (code + LLM), 21/09/2026:

    | Model lập bảng | Độ chính xác dev / test | Độ phủ dev / test | Giây/bài |
    |---|---|---|---|
    | `4b` (TUF) | 91 / 86% | 65 / 77% | ~29 |
    | Nemotron Ultra (OpenRouter free) | 90 / 82% | 63 / 79% | ~40 (dao động theo hàng chờ) |
    | Nemotron Ultra, 150 ứng viên theo ngưỡng nới, bảng 60 hàng | 84 / 77% | 75 / 96% | ~80 |

    Đã thử và **không** giữ lại (21/09/2026): gửi nhiều ứng viên hơn (đuôi danh sách 57–88% rác, model lấp bảng bằng chúng); xếp hạng bằng hồi quy logistic học từ 4 bài dev với đặc trưng tên bài/abstract/tiêu đề mục/in nghiêng/mẫu câu định nghĩa/độ rải/độ phổ biến tiếng Anh (`scripts/fit_term_ranker.py`, AUC dev 0,85 → 0,85, test 0,86 → 0,93 mà độ phủ top 40 không tăng); cắt ứng viên theo xác suất của mô hình đó (4b: 83/59% so với 91/65% ở dev). Script vẫn giữ để thử lại khi có thêm nhãn, vd. từ `data/user_terms.json`. Model mạnh không chọn thuật ngữ tốt hơn `4b` một khi prompt tách trường `kind` và có câu ngữ cảnh; nó chủ yếu dịch tự nhiên hơn (`secret key` → "khóa bí mật" thay vì "mã bí mật", `backward pass` → "lan truyền ngược"). Trần độ phủ nằm ở số ứng viên: 40 ứng viên đầu chỉ chứa 74/81% đáp án; gửi nhiều hơn thì model lấp bảng bằng cụm phổ thông. Đáp án là của một người chọn — hai người chọn "đâu là thuật ngữ" thường chỉ trùng 70–85%, nên đừng đọc các con số này chính xác tới từng điểm.
  - Vì sao cần: cùng một model, cùng một trang, hai lần chạy cho ra "Chúng ta gọi **attention**…" và "Chúng ta gọi **chú ý**…". Thử tắt phạt lặp (`presence_penalty`) không đổi được điều đó — chỉ có bảng thuật ngữ mới chốt được.
  - **Xem và sửa bảng ngay trên giao diện** (nút 📒 **Thuật ngữ**): bảng được lập ngay khi mở file, mỗi thuật ngữ có thể chọn **dịch** (sửa được cách dịch) hay **giữ nguyên tiếng Anh**, hoặc bỏ tích *Dùng* để không đưa vào prompt. Bấm **Lưu bảng** rồi dịch; trang đã dịch trước đó cần bấm "↻ Dịch lại" để áp bảng mới. Hàng "giữ nguyên" vẫn nằm trong prompt (ghi `term = term`) để model không tự dịch thuật ngữ đó.
  - **Nhập/xuất CSV** (nút ⬆ Nhập CSV / ⬇ Xuất CSV trong 📒): cùng định dạng glossary của PDFMathTranslate-next / BabelDOC — `source,target,tgt_lng`, có BOM để Excel mở đúng dấu. Cột `target` trống hoặc bằng `source` = giữ nguyên tiếng Anh; dòng có `tgt_lng` khác `vi` bị bỏ qua; thuật ngữ đã có thì được cập nhật. Nhập xong các hàng nằm trong bảng đang sửa, bấm **Lưu bảng** như thường (nên app vẫn học cách dịch bạn chọn). Xuất ra thì chỉ gồm các hàng đang tích *Dùng* — dùng lại cho bài khác, hoặc đưa cho `pdf2zh_next --glossaries`.
- **Tên bài** đi kèm prompt dịch, bảng thuật ngữ và phần trò chuyện. Lấy từ metadata, không có thì lấy dòng chữ to nhất trang đầu — **bỏ chữ xoay dọc ở lề**: con dấu `arXiv:1810.04805v2 [cs.CL] 24 May 2019` in to hơn cả tiêu đề nên trước đây cả 10 bài trong `data/` đều lấy nhầm con dấu làm tên bài. Lấy cả dòng và gộp các dòng liền nhau cùng cỡ chữ lớn nhất (tiêu đề dài nằm trên hai dòng; tiêu đề in hoa nhỏ có chữ cái đầu là span riêng → lấy span sẽ ra `RANSFORMER`). Sau khi sửa: 10/10 bài ra đúng tên.
- Prompt quy ước sẵn: "we" của nhóm tác giả dịch là **"chúng tôi"**; câu phải đủ ngữ pháp, rút gọn bằng cách chọn từ ngắn chứ không bỏ chủ ngữ hay từ nối.
- `Độ dài tối đa (× bản gốc)` (mặc định 1.3): gửi `max_len` cho từng đoạn; đoạn nào vẫn dài quá sẽ được LLM rút gọn thêm một lượt. Riêng **ô bảng** không cho dài hơn bản gốc (ô hẹp, hai bên đã có ô khác nên không nới được) và được nhắc dùng cách viết ngắn nhất hoặc giữ viết tắt tiếng Anh.
- Google Translate: dùng làm bộ dịch chính, hoặc tự động thay thế khi LLM lỗi kết nối hoặc bỏ sót đoạn.

## Model local đã thử (MacBook M2 16GB, Ollama, 17/09/2026)
Dịch 5 đoạn văn học thuật:
| Model | Thời gian | Nhận xét |
|---|---|---|
| **qwen3.5:latest (9B, tắt thinking)** ✅ | ~25 giây | Chính xác nhất, gọn (độ dài ≈ bản gốc), giữ thuật ngữ và trích dẫn |
| qwen3.5:4b-mlx | ~16 giây | Nhanh hơn nhưng dịch sai thuật ngữ nhiều hơn |
| translategemma:12b | ~130 giây | Văn mượt nhưng dài hơn 20–40%, dịch sai "random seeds" |
| translategemma:4b | ~43 giây | Mất chữ, dịch sai "overparameterized", bỏ "zero-shot" |
| HY-MT1.5-7B | ~200 giây | Dài hơn ~50%, sai nghĩa ("linearly" → "cấp số nhân"), lẫn chữ Trung |

OCR trang scan chất lượng thấp (72 / 60 dpi, JPEG nén mạnh):
| Engine | Độ khớp 72dpi | Độ khớp 60dpi | Thời gian |
|---|---|---|---|
| RapidOCR | 88,8% | 60,6% | 1–3 giây |
| Vision LLM qwen3.5:latest ✅ | 99,9% | 93,1% | 15–70 giây |
| Vision LLM glm-ocr | 99,8% | 84,9% | 30–45 giây |

→ Mặc định: **qwen3.5:latest** cho dịch; **RapidOCR + PP-DocLayout** cho OCR; khi chọn Vision LLM thì mặc định **PaddleOCR-VL-1.6** (`ollama pull hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF`) — đo 18/09/2026 trên PDF học thuật: đúng 98,4%, nhanh hơn GLM-OCR ~2–3 lần.

## Server llama.cpp TUF (GTX 1650 Ti 4GB, 15GB RAM) – 17/09/2026
Router `llm-router` (user service), cấu hình `~/.config/llama/models.ini`. Đã thêm preset `2b-vision`, `9b-vision` (có mmproj).

Dịch 5 đoạn học thuật:
| Preset | Thời gian | Nhận xét |
|---|---|---|
| **9b-uncensored** (Qwen3.5-9B Q4_K_M) ✅ | ~35 giây | Chính xác nhất trong mọi model đã thử, đúng thuật ngữ, độ dài ≈ bản gốc |
| gpt-oss (20B MoE) | ~35 giây | Tốt, gọn nhưng lẫn nhiều từ tiếng Anh (downstream, fine-tuning, edge), vài lỗi nhỏ |
| 2b (Qwen3.5-2B distill) | ~17 giây | Sai thuật ngữ (zero-shot → "không giám sát", baseline → "cơ sở dữ liệu") |
| phi4 (Phi-4-mini) | ~9 giây | Rất kém, sai nghĩa nhiều |

OCR trang scan xấu — đo lại 20/09/2026 bằng `scripts/bench_ocr.py`, đọc **từng vùng chữ** (cách app dùng thật), trang 3–6 của `attention.pdf`:

| Preset | Khớp 72 dpi | Khớp 60 dpi | Giây/vùng | VRAM |
|---|---:|---:|---:|---:|
| **paddleocr-vl** (ngl=99) ✅ | **97,0%** | 95,6% | 1,2–1,4 | ~2,0 GB |
| glm-ocr (ngl=99) | 95,6% | 95,7% | **0,8** | ~2,2 GB |
| 9b-vision (ngl=8, mốc cũ) | 99,7% | — | **147** | ~3,2 GB |

Điểm mấu chốt: hai model OCR chuyên dụng đủ nhỏ để đẩy **hết** lên GPU 4 GB (`ngl=99`), còn `9b-vision` chỉ nhét được 8 lớp nên chạy phần lớn trên CPU — **147 giây/vùng xuống còn 1,2 giây**. Một trang scan ~10 vùng: 25 phút → khoảng 12 giây. Gọi từ Mac qua Tailscale không chậm thêm (đo 0,5 giây/vùng khi model đã nóng).

→ PDF có lớp chữ: chọn preset **TUF**, model **`4b`** (xem mục dưới). PDF scan: `paddleocr-vl`, đọc từng vùng.
Chi tiết cách quét tham số và báo cáo đầy đủ: [docs/ocr-tuf.md](docs/ocr-tuf.md), kết quả nằm ở `~/vietpaper-ocr-bench/REPORT.md` trên TUF.

## Bench chất lượng dịch: Google Translate vs 2b / 4b / 9b (TUF) – 21/09/2026
```
.venv/bin/python scripts/bench_quality.py --systems google,2b,4b,9b   # dịch 5 trang bằng cả 4 hệ thống
.venv/bin/python scripts/judge_quality.py                             # trọng tài gpt-oss chấm mù 23 đoạn
.venv/bin/python scripts/quality_report.py && open bench/quality/report.html   # người chấm mù, đối chiếu
```
5 trang trong `data/` (Attention tr.4, BERT tr.3, RoFormer tr.4, DataSentinel tr.9 và tr.18) — có văn xuôi, công thức giữa câu và bảng ô hẹp. Mọi hệ thống chạy cùng điều kiện: **bảng thuật ngữ đóng băng** (lập một lần bằng `9b`, ghi ở `bench/quality/glossary.json`), `google_fallback=False` để đoạn bỏ sót hiện ra là bỏ sót, `temperature=0`, không gộp trang.

| Hệ thống | Giây/trang | tok/s | Đoạn bỏ sót | Mất công thức | Đúng thuật ngữ | Điểm nghĩa (trọng tài) |
|---|---:|---:|---:|---:|---:|---:|
| Google Translate | **2,8** | – | 0 | 0 | 318/424 | 2,97 |
| 2b | 238 | 59,3 | **8** | **6** | 276/366 | 3,32 |
| **4b** ✅ | **32,5** | 30,5 | 0 | 0 | 375/424 | 3,41 |
| 9b | 135 | 8,0 | 0 | 0 | 385/424 | **3,49** |

**Chọn `4b`.** So từng đoạn thì 4b và 9b **hoà 16/22 đoạn** (4b thắng 2, thua 4) — chênh lệch nằm trong sai số của 23 đoạn, trong khi 4b nhanh gấp **4,2 lần**. Lý do tốc độ không phải kích thước model mà là VRAM: `4b` nằm **trọn** trên GPU 4 GB (`ngl=99`, 30,5 tok/s), `9b` chỉ nhét được 19/49 lớp (`ngl=19`, 8,0 tok/s) — đúng hiện tượng đã gặp với OCR ở mục trên.

**Google Translate kém hơn, nhưng bằng chứng là cột thuật ngữ chứ không phải điểm trọng tài.** Nó giữ đúng cách dịch đã chốt **318/424** lần, so với 375 (4b) và 385 (9b) — đây là phép đếm chuỗi, xác định, trên 424 lần xuất hiện, không phải ý kiến của model nào. Theo điểm trọng tài thì Google thua 9–14 đoạn và chỉ thắng 3–4, nhưng với 23 đoạn chỉ có cặp *google vs 9b* đạt ý nghĩa thống kê (kiểm định dấu p = 0,013); *vs 4b* p = 0,118 và *vs 2b* p = 0,146 mới là xu hướng. Google vẫn nhanh nhất, không bao giờ bỏ sót đoạn và giữ nguyên 100% `{vN}`, nhưng dịch sạch thuật ngữ thành nghĩa đen: *"Scaled Dot-Product Attention"* → **"Sự chú ý đến sản phẩm chấm theo tỷ lệ"**, *dimension* → *"thứ nguyên"*. Cả 2b/4b/9b đều giữ nguyên thuật ngữ tiếng Anh. Google cũng không đọc được bảng thuật ngữ (chỉ LLM mới có), nên cột "đúng thuật ngữ" của nó là trùng hợp chứ không phải tuân thủ. → giữ đúng vai trò hiện tại: bộ dịch dự phòng khi LLM lỗi.

**Preset `2b` đang không dùng được** (lỗi cấu hình server, không phải model dở): đây là bản *reasoning-distilled*, **bỏ qua cả `reasoning_effort=none` lẫn `chat_template_kwargs.enable_thinking=false`** nên luôn suy nghĩ; llama.cpp để phần suy nghĩ ở `reasoning_content`, còn `content` rỗng. Trần token đầu ra của app (≈1,6 × độ dài bản dịch) bị phần suy nghĩ ăn sạch → app báo "LLM không trả về JSON" cho gần như mọi đoạn: đo lần đầu **2/14 đoạn**. Bench phải nới trần ×4 mới lên 10/14, và vẫn mất 6 công thức, bỏ sót 8 đoạn, 238 giây/trang. Sửa gốc: thêm `reasoning-budget = 0` vào khối `[2b]` của `~/.config/llama/models.ini` trên TUF rồi `systemctl --user restart llm-router`, sau đó chạy lại bench với `--think-scale 1`. 4b và 9b **không** dính lỗi này.

Chấm nghĩa: trọng tài là **`gpt-oss`** (20B, khác họ với Qwen 2b/4b/9b nên không thiên vị), chấm mù A/B/C/D với hoán vị riêng từng đoạn, thang 1–5 trên 3 trục (nghĩa / thuật ngữ / tự nhiên), chỉ chấm đoạn mà **cả 4 hệ thống** đều có bản dịch.

**Trọng tài này không đủ tinh để xếp hạng ba model** — `judge_quality.py` tự in ra phần chẩn đoán đó: nhãn A/B/C/D đã xáo ngẫu nhiên mà điểm trung bình của bốn nhãn vẫn lệch nhau **0,36 điểm**, trong khi khoảng cách 4b↔9b chỉ là 0,08. Tức là nhiễu vị trí của chính trọng tài lớn gấp bốn lần thứ cần đo. Nó cũng nén điểm vào khoảng 3–4 (chưa lần nào cho 5đ ở trục thuật ngữ) và để 17/23 đoạn đồng hạng nhất. Kết luận rút ra được từ nó chỉ là **"4b và 9b không phân biệt được"** (p = 0,69), đúng thứ cần cho quyết định trên. Muốn xếp hạng chắc chắn hơn: chấm tay trên `bench/quality/report.html`, dùng trọng tài mạnh hơn qua OpenRouter, hoặc chấm mỗi đoạn hai lượt với thứ tự đảo rồi lấy trung bình.

## Đọc đối chiếu: bôi đen, tô vàng, hỏi LLM
Trên mỗi ảnh trang có một **lớp chữ trong suốt** dựng từ chính lớp chữ của PDF (bản gốc lấy từ file gốc, bản dịch lấy từ trang đã dựng), mỗi dòng được kéo giãn ngang cho khớp đúng bề rộng dòng thật — nên bôi đen, copy chữ như đọc PDF bình thường. Dòng của mỗi đoạn được xếp lại theo thứ tự đọc: trang dịch đổ chữ Việt trước rồi mới dán công thức (dạng vector) đè lên, nên theo thứ tự nội dung PDF thì chữ của công thức rơi xuống cuối đoạn (`"… = 6 lớp giống nhau. … N x x dmodel"`). Công thức nằm **lọt giữa** khoảng ngang của một dòng (`"… nhỏ hơn |i| ."`) thì dòng đó được cắt ra để chen công thức vào đúng chỗ. Chỉ xếp trong phạm vi từng đoạn nên bài hai cột không bị trộn cột.
- **Rê chuột** lên một đoạn: đoạn tương ứng ở bản kia hiện **viền xanh** (chỉ để đối chiếu, không phải tô vàng). Số hiệu đoạn giống nhau ở hai bản (bản dịch được đặt lại đúng theo từng đoạn gốc) nên việc ghép cặp là chính xác, không phải đoán theo vị trí.
- **Bôi đen** → thanh nổi hiện hai nút:
  - **🖍 Tô vàng**: tô **đúng phần chữ đã bôi đen** — vài chữ thì tô vài chữ. **Mặc định hai bản tô độc lập** (tô ở đâu sáng ở đó; 25/09/2026). Bật ⚙️ Cấu hình → mục 5 “Đồng bộ vệt tô sang bản kia” thì bên bản kia tô **câu tương ứng**: chữ hai bản khác nhau nên không khớp được từng ký tự, nhưng trong một đoạn thì thứ tự câu giữ nguyên (prompt cấm model gộp/tách đoạn) nên câu thứ k bên này là câu thứ k bên kia; số câu hai bên lệch nhau thì mới tô cả đoạn. Bộ tách câu bỏ qua dấu chấm của viết tắt (`et al.`, `Fig.`, `e.g.`), chữ cái đầu tên người (`J. Smith`) và số thập phân, và nhập dấu chú thích đứng lẻ (`†`, `‡` — bản dịch hay đẩy xuống cuối câu) vào câu trước. Bôi đè lên vệt cũ thì nút đổi thành "Bỏ tô vàng". Bật **căn bằng LLM** thì bên bản kia thu về đúng cụm tương ứng (mục dưới). Lưu theo tài liệu trên đĩa (tải lại trang, khởi động lại app vẫn còn) và ghi thành **annotation highlight thật trong PDF tải về** (một annotation cho mỗi khung chữ, đúng phần đã tô).
  - **💬 Giải thích đoạn này**: mở khung trò chuyện với đoạn đang chọn đính kèm.
- **Căn vệt tô bằng LLM** (⚙️ Cấu hình → mục 5, mặc định bật, **`gpt-6-luna` qua proxy ChatGPT** – 24/09/2026, xem bảng dưới): bôi đen vài chữ rồi tô vàng → bản kia sáng **ngay** cả câu tương ứng (nhạt, viền đứt), LLM trả lời xong (~3 giây với `gpt-6-luna`) thì thu về **đúng cụm tương ứng**: "eschewing recurrence" → "loại bỏ sự hồi tiếp", "bộ nhớ ngắn hạn dài" → "long short-term memory". Hai chiều (bôi đen ở bản gốc hay bản dịch đều được). Chọn model riêng hoặc "dùng đúng server/model đang dịch"; bỏ tích thì luôn tô cả câu như trước.
  - App gửi câu chứa phần bôi đen (phần đó bọc `[[…]]`) và câu tương ứng bên kia (lệch số câu thì cả đoạn), LLM **chép nguyên văn** cụm tương ứng (`POST /api/<id>/align`, temperature 0, JSON ép khung). Server tìm lại cụm đó trong câu bên kia (so thẳng, không có thì bỏ qua hoa/thường, khoảng trắng, ngoặc kép kiểu in; trùng nhiều chỗ thì lấy chỗ gần vị trí phần bôi đen nhất). **Không tìm thấy → giữ tô cả câu**, không bao giờ tô bậy.
  - Không hỏi LLM khi không cần: bôi đen gần trọn các câu (≥ 90% chữ) mà số câu hai bản khớp nhau thì cả câu đã là đáp án. Bỏ vệt tô lúc LLM chưa trả lời thì kết quả bị bỏ. Kết quả lưu vào vệt tô (`via: "llm"`) nên mở lại bài, PDF tải về đều có đúng cụm, không hỏi lại.
  - **↻ Dịch lại một trang** không làm lệch vệt tô: vệt tô ở bản dịch neo theo vị trí chữ trong bản dịch cũ, nên trước khi dịch lại app chuyển neo của nó sang phần tương ứng ở bản gốc (cụm LLM đã căn, không thì cả câu); dịch xong thì tính lại phần tương ứng trong bản dịch mới (hỏi LLM đúng cụm nếu bật). Ghi chú gắn với vệt vẫn theo vệt. Thử 25/09/2026: tô "mô hình chính thức" ở bản dịch → dịch lại → vẫn tô đúng "formal model" / "mô hình chính thức".
  - **Độ chính xác** (24/09/2026, `scripts/bench_align.py --gold bench/align_gold.json`): 60 cụm từ bài FIDES, chọn ngẫu nhiên kiểu người bôi đen (1/3 là chiều Việt → Anh), đáp án ghi tay — đáp án là đoạn liền nhỏ nhất ở bản kia chứa bản dịch của mọi chữ đã bôi đen, nên bản dịch chen chữ vào giữa thì đáp án gồm luôn ("perform future" → "thực hiện các lời gọi công cụ sau này"):

    | Model | Chạy ở đâu | Đúng | Thiếu/thừa một phần | Sai chỗ | Trung vị / chậm nhất |
    |---|---|---|---|---|---|
    | **`gpt-6-luna`** (mặc định) | proxy ChatGPT OAuth trên TUF | **56** | 4 | 0 | 3,0 s / 12,1 s |
    | `gpt-oss` (20B) | llama.cpp TUF | 56 | 3 | 0 (+1 không tìm thấy) | 5,3 s / 14,7 s |
    | `gpt-5.6-terra` | proxy ChatGPT OAuth trên TUF | 53 | 7 | 0 | 1,9 s / 5,5 s |
    | `qwen/qwen3.8-27b:free` | OpenRouter (free) | 51 | 9 | 0 | 1,0 s / 3,8 s |
    | `4b` | llama.cpp TUF | 45–46 | 13–14 | 0 (+1 không tìm thấy) | 0,8 s / 1,5 s |
    | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter (free) | 45 | 15 | 0 | 1,6 s / 38,6 s |
    | `dots-studio/dots-3-note-preview:free` | OpenRouter (free) | 41 | 19 | 0 | 1,5 s / 3,7 s |
    | `9b` | llama.cpp TUF | 37 | 23 | 0 | 2,1 s / 10,1 s |
    | `inclusionai/ling-3.0-flash-fin:free` | OpenRouter (free) | 37 | 21 | **2** | 1,0 s / 1,9 s |

    Không đo được (24/09/2026): Gemma 4 31B/26B, GLM 5.2, Laguna, Nemotron Super (OpenRouter free, liên tục 429/quá tải), Inkling (403 với tài khoản này), Nemotron 3.5 Lightning (treo > 60 s), nex-n2.5-pro (8–17 s/lần, dừng giữa chừng), `gpt-5.6-luna` (thử 3 cụm thì thiếu chữ 2 — dừng cho đỡ tốn hạn mức tài khoản free), `gpt-5.5` (proxy báo không có). Bench mặc định dùng `qwen/qwen3.8-27b:free` trên OpenRouter (nhanh, không tốn tiền); model free trên OpenRouter được gọi với `reasoning: {enabled: false}`.

    **Proxy ChatGPT** (`http://192.168.1.10:10531/v1`, tài khoản ChatGPT web **free**, chỉ vào được trong mạng LAN): hết hạn mức hoặc máy không ở cùng mạng thì lượt căn báo lỗi → giữ tô cả câu, đổi sang OpenRouter trong ⚙️ Cấu hình. `gpt-6-luna` **không nhận `temperature`**: không stream thì trả 500 "Bad Request", stream thì trả 200 rồi ngắt kết nối — app tự hỏi lại không có `temperature` và nhớ model đó (áp dụng cho mọi chỗ gọi LLM: dịch, thuật ngữ, trò chuyện). Có preset "ChatGPT (proxy OAuth trên TUF)" ở mục dịch / trò chuyện / thuật ngữ / căn vệt tô.

    Không model nào tô sai chỗ; lỗi là thiếu chữ ("secrecy if" → "bí mật tường minh", thiếu "nếu") hoặc thừa chữ. 4 cụm `gpt-6-luna` còn sai thì 2 là cụm cắt đôi từ ("[Nội] dung công[cụ]"), vốn không có đáp án rõ. Đã thử và **bỏ**: prompt chỉ có luật, không ví dụ (`4b` ~18/30 theo mắt chấm, hay lấy thừa); bắt model kê bản dịch từng chữ rồi server tô từ mảnh đầu tới mảnh cuối (`4b` 46/60, chậm gấp đôi); cho `4b` tự kiểm lại câu trả lời của mình (43/60, tệ hơn). Giữ lại: **hỏi lại một lần** khi model trả rỗng/trả cụm không có thật — `gpt-oss` thỉnh thoảng trả `""` ở lần hỏi đầu rồi hỏi lại thì đúng (51 → 56 cùng với việc nhận thêm "các thiết kế", "tấn công" là đáp án đúng).
  - **Nếu chọn model trên router llama.cpp TUF**: router chỉ giữ một model trong bộ nhớ — đang ở `4b` mà gọi `gpt-oss` thì mất ~11 s nạp, quay về `4b` ~6 s. Căn bằng `gpt-oss` trong lúc đang dịch bằng `4b` là mỗi lần tô hai bên phải nạp lại model.
  - TUF chỉ có 1 lượt song song: đang "Dịch tất cả" thì lượt căn phải xếp hàng sau trang đang dịch (vệt vẫn là cả câu, viền đứt, tới khi xong).
- **Tải lại trang** không mất gì: app nhớ tài liệu cuối và lấy lại bản dịch, tô vàng, bảng thuật ngữ từ server (`GET /api/<id>/state`). Khởi động lại app cũng không mất — xem mục dưới.

## Lưu tài liệu, mở lại PDF đã tải lên (21/09/2026)

Mỗi PDF tải lên được lưu trong `data/docs/<id>/` (`store.py`): `src.pdf` (bản gốc), `out.pdf` (bản dịch đang dựng), `state.json` (tên, trang đã dịch, bản dịch từng đoạn, khối đã trích/OCR, tô vàng, bảng thuật ngữ). Nút **📚 Tài liệu** liệt kê mọi bài đã lưu (tên bài, số trang đã dịch, số vệt tô, lần cuối, dung lượng) để mở lại hoặc xoá hẳn (🗑, có hỏi lại, không hoàn tác được). Không tự xoá theo thời gian.

- **Khởi động lại app** không mất gì: tài liệu chưa có trong bộ nhớ thì được nạp lại từ đĩa ở request đầu tiên (mất vài ms), mọi endpoint chạy như cũ. Trang đã dịch không gọi LLM lại, trang đã OCR không OCR lại, số hiệu đoạn giữ nguyên nên tô vàng vẫn khớp.
- **Tải lên lại đúng file cũ** (so bằng SHA-256, tên file khác cũng được) → mở lại bản đã lưu, không tạo bản mới, không dịch lại.
- **Khi nào ghi**: `out.pdf` sau mỗi lượt dịch (một lần cho cả nhóm trang); `state.json` sau mỗi lần tô vàng / sửa thuật ngữ / dịch xong / OCR xong một trang. Ghi nguyên tử (file tạm + `os.replace`): tắt app giữa chừng thì còn bản cũ nguyên vẹn. `state.json` chỉ ghi nhận trang đã có trong `out.pdf` trên đĩa, nên tắt app đúng lúc đang dịch thì mở lại trang đó là "chưa dịch" chứ không phải "đã dịch" mà ảnh vẫn là bản gốc.
- **Chi phí** (Attention, 15 trang, đo 21/09/2026): ghi `out.pdf` ~0,6 s/lần, không tăng theo số trang; `out.pdf` 4 MB, `state.json` 180 KB. Phải ghi với `garbage=4`: mỗi trang dịch nhúng lại nguyên font, `garbage=3` không gộp được nên file lên 16,6 MB và mỗi lần ghi chậm dần tới ~1 s.
- API: `GET /api/docs` (danh sách), `DELETE /api/docs/<id>` (xoá). `data/*.pdf` (bài dùng cho bench) và file tải lên kiểu cũ `data/<id>.pdf`, `data/<id>_vi.pdf` không bị đụng tới — xoá tay được.
- **💬 Hỏi về tài liệu**: hỏi bất cứ điều gì về bài. LLM nhận: tên bài, bảng thuật ngữ, **mục lục bài** (rút từ các dòng dạng `3.2 Scaled Dot-Product Attention`), rồi đoạn đang bôi đen, rồi nội dung trang đang xem và các trang bên cạnh — **kèm cả bản gốc lẫn bản dịch của từng đoạn** — nhồi tới 55% ngữ cảnh. Trả lời chảy về từng chữ (streaming). Mặc định dùng đúng server/model đang dịch; bỏ tích "Dùng đúng server/model đang dịch" trong ⚙️ Cấu hình để chọn model khác (ví dụ dịch bằng model chuyên dịch, còn hỏi đáp bằng Qwen3.5).
  - **Câu trả lời hiện đúng công thức, bảng và ký tự đặc biệt** (`static/render.js`): Markdown (marked) + công thức LaTeX (KaTeX) + lọc HTML (DOMPurify), thư viện nằm sẵn trong `static/vendor/` (~600 KB, không gọi CDN — tải lại bằng `.venv/bin/python scripts/download_vendor.py`; thiếu file thì chat lùi về chữ thô chứ không hỏng). Prompt trò chuyện bảo model viết công thức bằng `$…$` / `$$…$$` và so sánh bằng bảng Markdown — trước đây prompt **cấm** LaTeX nên có dựng engine cũng không có gì để dựng. Nút **📋 Chép** chép bản Markdown gốc (còn nguyên LaTeX), không phải chữ đã dựng.
  - Đang chảy chữ thì bong bóng được vẽ lại ~12 lần/giây; mỗi công thức chỉ dựng bằng KaTeX **một lần** rồi nhớ lại, nên công thức hiện ngay và không nhấp nháy, còn `$$` đang gõ dở thì hiện chữ thô thay vì lỗi đỏ. Đo 21/09/2026 trên câu trả lời thật của `4b` (TUF): 970 lần lấy mẫu trong lúc chảy, 60 công thức + 2 bảng, **0 lần công thức biến mất, 0 khung lỗi đỏ**; vẽ ~0,3 ms/lần.
  - Câu trả lời LLM là dữ liệu không tin được (bài báo có thể chứa câu dặn model làm gì đó): HTML thô trong câu trả lời **không bao giờ thành thẻ** (dấu `<` bị thoát, và marked được cài để thoát cả HTML thô nó tự nhận ra — kẽ hở "\\`<div …>`" tìm thấy khi rà 11/10/2026), thẻ dựng được giao diện giả (`form`, `input`, `button`, `style`) bị bỏ, `style` có `url(…)` hay `position:fixed/absolute` bị bỏ, khung trả lời giới hạn bằng `contain: paint`; mọi thẻ trình duyệt **tự tải** khi hiện ra (`img`, `video`, `iframe`…) bị bỏ — nếu không, một câu kiểu "chèn ảnh `https://…/?q=<tóm tắt cuộc trò chuyện>`" là đủ để nội dung bị gửi ra ngoài lúc câu trả lời hiện lên. Link thường vẫn giữ (phải bấm mới đi).
  - Sửa `static/render.js` xong thì mở **http://127.0.0.1:8000/render-test.html** (đã đăng nhập): 24 ca (công thức 4 kiểu dấu, `$5` không phải công thức, `$` trong code, bảng, LaTeX hỏng, chặn tự tải, các lối lách HTML/CSS…) và giả lập chảy chữ từng ký tự, không cần server LLM.


## Ghi chú (25/09/2026)

Khung bên phải có hai tab **💬 Trò chuyện | 📝 Ghi chú** (nút 📝 Ghi chú trên thanh trên cùng mở thẳng tab ghi chú). Ghi chú lưu cùng tài liệu trên đĩa (`notes.py`, trong `state.json`) nên khởi động lại app vẫn còn; chữ ghi chú là Markdown, viết được `$công thức$` và bảng (dựng bằng `render.js` như câu trả lời LLM).

- **Ghi chú chung cho cả bài**: ô ở đầu tab, tự lưu sau 1 giây ngừng gõ. Bản đang gõ được giữ trong trình duyệt tới khi server nhận, và gửi nốt khi đóng tab / chuyển sang bài khác (chuyển bài giữa lúc chờ vẫn lưu đúng bài cũ).
- **Ghi chú cho một đoạn**: bôi đen → **📝 Ghi chú** trên thanh nổi. Đoạn chưa tô thì tô vàng luôn rồi gắn ghi chú vào vệt đó; bôi đè lên vệt có sẵn thì gắn vào vệt đó. Vệt có ghi chú hiện dấu **📝** ở cuối (cả hai bản), góc trang gốc hiện số ghi chú của trang — bấm để mở đúng ghi chú. Bỏ vệt tô **không xoá** ghi chú (thẻ ghi chú hiện "vệt tô đã bỏ").
- **Lưu câu trả lời LLM**: nút **📌 Lưu thành ghi chú** dưới mỗi câu trả lời — giữ nguyên Markdown/LaTeX, kèm câu hỏi, tên model, đoạn đã bôi đen lúc hỏi (và gắn vào vệt tô nếu đoạn đó đã được tô).
- **Hỏi lại**: nút **💬 Hỏi lại** trên một ghi chú → sang tab trò chuyện, ghi chú đó được gửi kèm câu hỏi kế tiếp (`note_ids` trong `/api/<id>/chat`, trước đoạn bôi đen và các trang, tối đa ~1/4 ngân sách ngữ cảnh).
- Mỗi thẻ: tới chỗ đó trong bài, ✏️ sửa (Ctrl+Enter lưu, xoá hết chữ = xoá ghi chú), 📋 chép Markdown, 🗑 xoá.
- **Xuất**: **⬇ Xuất .md** (`GET /api/<id>/notes.md`: ghi chú chung, rồi theo trang, kèm đoạn trích và câu hỏi LLM). **PDF tải về** có ghi chú: ghi chú của vệt tô là nội dung popup của highlight đó; ghi chú của trang / vệt đã bỏ / cả bài là biểu tượng ghi chú ở lề trên trang.
- API: `GET/POST /api/<id>/notes` (POST không có id = thêm, có id = sửa chữ; id `general` = ghi chú chung), `DELETE /api/<id>/notes/<note_id>`. `/state` và `📚 Tài liệu` có số ghi chú.

## Đọc, trò chuyện, kiểm soát bản dịch (25/09/2026)

**Khung đọc** — bản gốc và bản dịch nằm trong **hai khung xem độc lập**: mỗi khung tự cuộn (dọc lẫn ngang) và tự thu phóng, không còn kéo nhau lên xuống. Thanh ở giữa kéo để chia bề rộng hai khung (nhớ lại; bấm đúp: chia đôi). Thanh ở góc dưới trái:
- **Gốc | Cả hai | Dịch**: xem một bản thì khung đó chiếm cả chỗ. Mỗi khung nhớ chỗ đang đọc, ẩn rồi hiện lại vẫn ở đúng chỗ.
- **− % +** áp cho **khung đang chỉnh** (khung chuột vừa rê vào, tên hiện cạnh số %); ⌘/Ctrl + lăn chuột hoặc chụm hai ngón trên trackpad thì phóng khung dưới con trỏ, quanh đúng điểm con trỏ. Từ 40% tới 400%, mỗi khung nhớ mức riêng. Ảnh trang được vẽ lại nét hơn khi phóng (server vẽ PNG ở zoom 3–4 thay vì 2). Lớp chữ, tô vàng, ghi chú vẫn dùng được ở mọi mức phóng.
- **🔗 Cuộn cùng nhau** (mặc định tắt): cuộn khung đang dùng thì khung kia theo tới cùng trang, cùng vị trí trên trang.
- **Kính lúp**: giữ ⌥ Option / Alt khi rê chuột trên trang → khung soi chữ nhật 440×260 (đọc trọn một dòng), phóng ×1,8; ⌥ + lăn chuột đổi độ phóng trong khoảng ×1,4–×3 (nhớ lại, số hiện ở góc kính). Tải riêng ảnh ×4 của trang cho nét, trong lúc chờ soi tạm ảnh đang hiện.
- **Khung trò chuyện kéo rộng/hẹp** ở mép trái (nhớ lại bề rộng, bấm đúp về mặc định, nút ⤢ mở nửa màn hình).

**Trò chuyện**:
- **Phiên**: server giữ lịch sử từng phiên (`chats.py`, `data/docs/<id>/chats.json`) — tải lại trang, khởi động lại app vẫn còn. Menu 🗂 đầu khung: danh sách phiên, mở lại, đổi tên, ⬇ tải `.md`, xoá; **＋ Mới** mở phiên mới (phiên cũ vẫn giữ). Mở tài liệu là mở lại phiên gần nhất. **📝 Tổng hợp**: LLM tổng hợp cả phiên thành một ghi chú (Câu hỏi chính · Điều rút ra · Khái niệm, công thức · Còn bỏ ngỏ), sửa được như ghi chú thường; tổng hợp lại (sau khi hỏi thêm) thì cập nhật đúng ghi chú đó.
- **■ Dừng** giữa chừng (giữ phần đã trả lời, server lưu kèm dấu “đã dừng”), **↻ Trả lời lại** và **✏️ Sửa** câu hỏi cuối.
- Hiển thị: chữ đang chảy chỉ vẽ lại khối Markdown cuối (khối đã xong giữ nguyên — không chậm dần theo độ dài, bôi đen chép được giữa chừng), xong thì dựng cả câu trả lời một lần; code được tô màu (highlight.js) có tên ngôn ngữ và nút chép; chỉ tự cuộn khi đang ở đáy (cuộn lên đọc thì hiện “↓ Mới nhất”).
- **Nguồn ngữ cảnh** (ô cạnh nút Gửi): Tự động · Gốc + dịch · Chữ gốc · Bản dịch · 🖼 Ảnh trang · 📄 Cả bài · 📎 Nguyên PDF. Tự động theo luật cố định (không tốn thêm lượt gọi): bôi đen → đoạn đó + trang của nó; “trang 7” → trang 7; “Hình 2” / “Bảng 3” → trang có chú thích đó; “công thức (3)” → trang có số hiệu (3); câu hỏi về cả bài (tóm tắt, đóng góp, kết luận, hạn chế…) → cả bài nếu vừa ngữ cảnh, không thì trang đầu + trang kết luận; hỏi về bản dịch → gốc + dịch; ngữ cảnh hẹp → chỉ chữ gốc. Thêm **trang liên quan** theo từ khoá (BM25 thu gọn trên chữ gốc và bản dịch; thuật ngữ tiếng Việt trong câu hỏi được đổi ngược sang tiếng Anh theo bảng thuật ngữ). Không hỏi về cả bài thì ngữ cảnh tối đa ~12k token. Dưới mỗi câu trả lời: các trang đã gửi (🖼 = gửi ảnh), model, và lý do (rê chuột).
- **📎 Nguyên PDF**: gửi cả file PDF gốc kèm mỗi câu hỏi, thay cho chữ app trích (bảng thuật ngữ, mục lục, đoạn bôi đen vẫn gửi; hỏi về bản dịch thì kèm bản dịch trang liên quan). Chỉ **OpenRouter** (plugin file-parser: `pdf-text` trích chữ miễn phí; `native` — model tự đọc PDF, thấy cả hình — với Claude, Gemini, GPT-4o trở lên) và **OpenAI**. Server khác (proxy ChatGPT lặng lẽ bỏ file, llama.cpp trả 400, Ollama/LM Studio không có kiểu nội dung này), file > 20 MB, bài dài hơn ngữ cảnh model, hay server từ chối file → app tự gửi chữ cả bài và ghi lý do dưới câu trả lời. Đo 25/09/2026 (`nex-n2.5-pro:free`, 4 câu khó của bench: công thức (1), Bảng 2 BLEU, Bảng 1 GLUE, số bước pre-train BERT): 4/4 đúng, ~15 s/câu. Đổi lại mỗi câu hỏi gửi lại cả bài (Attention ~11k, BERT ~19k token) — model trả phí thì tốn gấp vài lần chế độ Tự động.
- **Ảnh trang** (sửa 03/10/2026: chỉ gửi khi chữ không đủ): câu hỏi về **hình** — “Hình 2”, biểu đồ, đồ thị, sơ đồ, trục, đường cong, “trong hình” (chữ trích từ PDF chỉ có chú thích của hình) — → gửi ảnh trang; **bảng** gửi dạng lưới chữ đủ số liệu (xem mục dưới), chỉ gửi ảnh khi bảng không dựng được thành chữ (trang scan, lớp chữ rác, bảng là ảnh); **công thức** gửi dạng LaTeX, chỉ gửi ảnh khi công thức được hỏi chưa có LaTeX (tắt nhận dạng, server nhận dạng lỗi, kết quả không khớp glyph — app chờ nhận dạng xong rồi mới quyết). Trước đây mọi câu nhắc tới bảng hay công thức đều gửi ảnh, nên model xem ảnh (thường yếu hơn ở suy luận) trả lời thay model trò chuyện. “hình”/“bảng” chỉ tính khi có số — “Hình 2” — hoặc trong cụm như “trong hình”, “hình vẽ”, “bảng kết quả”: tiếng Việt viết rời âm tiết nên “mô hình”, “hình thức”, “bảng thuật ngữ” đều chứa đúng chữ đó (trước khi sửa, “Mô hình này khác BERT thế nào?” bị gửi ảnh); “graph” không tính (đồ thị cấu trúc trong bài ML). Đoạn bôi đen chỉ tính là hỏi về hình/bảng khi nó là chú thích (“Figure 3: …”), còn đoạn văn chỉ nhắc tới hình thì hỏi gì theo câu hỏi. Ảnh (×1,5) gửi cho **model xem ảnh** (⚙️ Cấu hình → mục 3; mặc định OpenRouter `dots-studio/dots-3-note-preview:free`; `nex-agi/nex-n2.5-pro:free` chọn trước đó bị OpenRouter gỡ ngày 25/09/2026 — cài đặt cũ còn để model đó được tự đổi). Proxy ChatGPT **không nhận ảnh** (trả 500 “URL scheme must be http or https, got data:”, thử 25/09/2026). Thử trên trang 4 của Attention: `nex-n2.5-pro:free` chép đúng công thức Attention bằng LaTeX và nói đúng Figure 2 (8 s, trả lời tiếng Việt); `dots-3-note-preview:free` đúng (3 s); Gemma 4 / Qwen 3.8 free đang bị giới hạn tần suất. Model trò chuyện đã đọc được ảnh (theo danh sách model của OpenRouter, nhớ trong `data/model_meta.json` qua các lần khởi động lại) thì dùng luôn nó. Model xem ảnh lỗi (429, quá tải) thì app tự hỏi lại model trò chuyện chỉ với chữ và ghi lý do dưới câu trả lời.
- **Bảng → lưới chữ** (03/10/2026): chữ gửi LLM được dựng từ các đoạn để dịch, mà ô chỉ có số thì không có gì để dịch nên bị bỏ — bảng chỉ còn tiêu đề và tên hàng, **không có số liệu nào** (Bảng 2 Attention, Bảng 1 BERT); hỏi về bảng là buộc phải gửi ảnh. Nay mỗi vùng `table` của PP-DocLayout được dựng lại thẳng từ lớp chữ PDF thành bảng Markdown (`page_tables`, đặt đúng chỗ bảng trong chữ của trang: `[bảng]` rồi lưới; chế độ gốc + dịch thì ô đã dịch ghi `gốc → dịch`): số mũ giữ cấu trúc (`3.3 · 10^{18}` thay vì `3.3 · 1018`), ô in đậm (kết quả tốt nhất) bọc `**…**`, tiêu đề nhiều tầng gộp theo cột (`BLEU EN-DE`, `French HellaS` — nhóm lấy theo nét `\cmidrule` dưới tiêu đề, không kẻ thì theo các ô con nằm cân hai bên), ô gộp nhiều cột chép vào mọi cột nó phủ, nhãn nhóm `\multirow` căn giữa nhóm (“(A)” của Bảng 3 Attention) chuyển lên hàng đầu nhóm theo đường kẻ ngang, cột lấy theo hàng nhiều ô nhất (bảng thưa thì hàng “phổ biến nhất” lại là hàng có ô gộp 7 cột). PyMuPDF có khi gộp ô của các cột liền nhau thành một dòng (`69.5% 77.9% 68.7%` ở bảng Mistral, khe 0,68 cỡ chữ) — tách ở khoảng trống > 0,4 cỡ chữ nằm trên “kênh trống” dọc của bảng; khoảng giãn của chữ căn đều cũng rộng như vậy (0,66 ở bảng DataSentinel) nhưng không thẳng hàng qua các dòng nên không bị cắt. Sửa kèm cho cả bản dịch: ô chỉ có “-” không còn bị coi là từ ngắt dòng mà nối với ô bên dưới (Bảng 2 BERT: `- 81.2` lên nhầm hàng). Soát cả 66 bảng của 10 bài bench, đối chiếu với ảnh ~10 bảng khó nhất (Bảng 2, 3 Attention; Bảng 2, 8 BERT; các bảng Mistral, Mixtral): bảng số liệu ra đúng hàng/cột; bảng chuỗi tấn công của DataSentinel (chữ vô nghĩa, ô nhiều dòng) vẫn lộn xộn. Dựng bảng ~0,01 giây/trang, dùng chung kết quả layout với công thức (`page_regions`, ~0,8 giây/trang, một lần). Trang scan (khối OCR không giữ ô số) chưa có lưới: hỏi về bảng thì gửi ảnh.
- **Sửa lỗi**: trước đây công thức trong chữ gửi LLM là chỗ giữ `{v1}` — LLM không thấy công thức nào. Nay là chữ của công thức lấy từ glyph trong PDF, đặt trong `⟦…⟧` (`⟦q·k=Pdk i=1qiki⟧` — lộn xộn nhưng đủ biết công thức nói gì; cần chính xác thì hỏi kèm ảnh trang). Công thức **đứng riêng dòng** (layout bỏ khi trích đoạn để dịch) được gom lại từ các dòng chữ nằm ngoài mọi đoạn (≥ 30% ký tự font công thức, hoặc có “=” và số hiệu) và chèn ngay sau đoạn phía trên nó: `[công thức] ⟦Attention(Q, K, V ) = softmax(QKT √dk )V⟧`. Ma trận nhiều tầng (RoFormer) ra chữ rất rối — hỏi kèm ảnh.
- **Công thức → LaTeX** (⚙️ Cấu hình → mục 3, mặc định bật): chữ glyph mất cấu trúc hai chiều — ma trận xoay (15) của RoFormer ra `−sin mθ1 · · · cos mθ1 0 0 …` lẫn lộn và dính cả (14), (16), chữ trong Hình 1; gạch phân số, căn là nét vẽ nên mất hẳn. Nay app cắt ảnh từng công thức và cho model đọc ảnh chép ra LaTeX: công thức đứng riêng dòng theo vùng `display_formula` của PP-DocLayout (gọn từng công thức, số hiệu `formula_number` tách riêng → `[công thức (15)] ⟦$$\mathbf{R}^d_{\Theta,m} = \begin{pmatrix}…$$⟧`), và công thức trong dòng có nhiều tầng (chân chữ lệch > 4,5pt: phân số, tổng có cận). Mặc định dùng model OCR ở mục 2 (PaddleOCR-VL local, prompt `Formula Recognition:` — không gửi dữ liệu ra ngoài), hoặc model xem ảnh. Kết quả được đối chiếu với glyph (bội tập chữ cái/số phải khớp ≥ 60%: chặn chép nhầm vùng, lặp vô hạn) — không khớp thì giữ glyph. Làm một lần cho mỗi công thức, lưu trong `state.json`; khi trò chuyện, trang chính chờ tối đa 30 giây, các trang khác chạy nền; server nhận dạng không chạy thì dùng glyph và 10 phút sau mới thử lại. `POST /api/<id>/formulas` làm trước cả bài. Đo 25/09/2026, RoFormer trang 5: 8/8 công thức ra LaTeX đúng (cả ma trận 7×7), 16 giây với PaddleOCR-VL trên Mac M2. Làm trước cả bài: Attention 13 công thức / 22 s, DataSentinel 18 / 75 s, RoFormer 59 / 146 s. Công thức trong dòng được cắt từ bản sao trang chỉ còn ký tự của công thức (như khi đặt công thức vào bản dịch): cắt thẳng từ trang thì model chép cả chữ câu văn bên cạnh (“… in generating the”) — trước khi sửa 5 công thức bị loại, sau khi sửa 8/8 trên hai trang của DataSentinel. Bench (`--no-see`, 6 câu về công thức: Attention (1), (3), RoFormer (15), (16), DataSentinel (3), (4); `dots-3-note-preview:free`): bật LaTeX 6/6, 8,0 s/câu; tắt 6/6, 11,4 s/câu — model này đủ mạnh để tự dựng lại công thức từ glyph rối (và RoPE quá nổi tiếng), nên lợi ích chính là câu trả lời nhanh hơn và đỡ phụ thuộc model; model yếu hơn (local) mới thấy khác biệt về độ đúng.
- **Bench chọn ngữ cảnh** (`scripts/bench_chat_ctx.py`, 20 câu hỏi trên Attention + BERT trong `bench/chat_ctx.json`, chấm bằng regex đáp án; phần lớn câu hỏi đặt khi đang xem trang 1 để buộc app tự tìm trang). 25/09/2026, `nex-agi/nex-n2.5-pro:free`, so với cách cũ `near` (trang đang xem + lân cận): **Tự động 20/20, cách cũ 18,5/20**; 0/2 câu bẫy “mô hình” bị gửi ảnh; thời gian như nhau (~34 s/câu, model free). Hai câu cách cũ sai: “Pre-train bao nhiêu bước, batch bao nhiêu?” (BERT — đáp án ở tr.13, ngoài 11 trang vừa ngân sách: model trả lời “không nêu”), “BLEU EN-FR theo Bảng 2” (đọc nhầm 41.0 thay vì 41,8; Tự động gửi ảnh bảng nên đúng). **Bench này phân biệt kém**: bài chưa dịch chỉ gửi chữ gốc, Attention cả bài < 12k token nên cả hai chế độ đều gửi đủ 15 trang; hai bài lại quá nổi tiếng nên model trả lời được cả hình/công thức từ trí nhớ. Muốn đo đúng phần tìm trang thì chạy với ngân sách nhỏ (`--budget`, như model local 8k) hoặc bài ít người biết. **03/10/2026, bảng**: thêm 10 câu có đáp án **chỉ nằm trong ô bảng** (không có trong đoạn văn — Attention Bảng 2, 3; BERT Bảng 1, 2; Mixtral Bảng 4, 5; Mistral Bảng 2; GQA Bảng 1), chạy chỉ gửi chữ (`--no-see --kinds table`, `inclusionai/ling-3.0-flash-sante:free`): trước khi dựng lưới bảng **1,5/12** (2 câu được điểm vì con số tình cờ có trong đoạn văn; còn lại model trả lời “các ô số bị thiếu trong đoạn trích”), sau khi sửa **12/12**. Mỗi câu có thêm `img` = chế độ Tự động có nên gửi ảnh không; script in số câu gửi ảnh đúng dự kiến.
- API: `POST /api/<id>/chat` (`question`, `session_id`, `context`, `regenerate`, `edit`; trả header `X-Session-Id`, `X-Context`), `GET /api/<id>/chats`, `GET/PATCH/DELETE /api/<id>/chats/<sid>`, `GET /api/<id>/chats/<sid>.md`, `POST /api/<id>/chats/<sid>/summarize`.

**Kiểm soát bản dịch**:
- **Dịch theo dải trang**: ô cạnh nút Dịch — `1, 3, 5, 9-13`, `10-` (tới hết), `-5` (từ đầu); để trống = cả bài. Tích “gồm trang đã dịch” để dịch lại cả trang đã dịch trong dải (vẫn gộp trang như thường).
- **⛔ Không dịch một đoạn** (bảng, code, công thức dễ vỡ bố cục): bôi đen → ⛔ trên thanh nổi, hoặc bật **⛔ Chọn đoạn** ở thanh dưới trái rồi bấm vào từng đoạn. Bản dịch giữ nguyên chữ gốc ở đó (đoạn gạch chéo đỏ); trang đã dịch được dựng lại ngay từ bản dịch đã lưu, không gọi LLM. Bỏ ⛔ một đoạn chưa từng dịch thì app dịch riêng đoạn đó. Dấu ⛔ nhớ theo chữ của đoạn: đổi OCR/layout mà số thứ tự đoạn đổi thì dấu theo đúng đoạn; đoạn không còn (chữ trích khác đi) thì bỏ dấu và ghi vào log.
- **⤳ Giữ gốc** cả trang: bản dịch dùng nguyên trang gốc, không gọi LLM (“Dịch trang này” để dịch lại).
- **Không dịch Tài liệu tham khảo** (mặc định bật, ⚙️ Cấu hình → mục 1): từ tiêu đề References tới mục kế tiếp — tiêu đề Phụ lục (“Appendix”, “A  Training Stability”), tiêu đề cùng kiểu chữ với tiêu đề References (phụ lục không đánh số như “Attention Visualizations”), hay chú thích hình/bảng — theo thứ tự đọc của PDF nên đúng cả khi danh mục chảy qua hai cột (đoạn gạch chéo xám). Một đoạn thuộc phần này khi ít nhất nửa chiều cao của nó nằm trên các dòng đó (`refs_share`): PyMuPDF hay tách mỗi dòng của một mục thành một khối riêng (dòng sau thụt vào), còn đoạn để dịch là cả mục, nên cách cũ (thử tâm đoạn) để tâm rơi vào khe giữa hai dòng và vẫn gửi mục đó đi dịch. Đo 15/10/2026 trên 10 bài bench: trước khi sửa 85/414 khối `reference_content` vẫn bị dịch (Attention 26/40, RoFormer 24/44, DataSentinel 13/51, FlashAttention-2 7/19, Mixtral 6/35, Mistral 4/29, RAG 4/70, BERT 1/48), sau khi sửa 0/414; 85 khối mới được giữ nguyên đều là mục tài liệu, không đoạn nào thôi được giữ nguyên, không đoạn Phụ lục/chú thích hình nào mới bị giữ nguyên nhầm. Chú thích hình/bảng kết thúc phần References dù dòng đầu dài bao nhiêu; giới hạn dòng đầu dưới 80 ký tự chỉ áp cho tiêu đề Phụ lục và tiêu đề in đậm, vì mục tài liệu dài có thể trông như tiêu đề phụ lục (“A lite bert for…”, RoFormer tr.14). Trước 15/10/2026 giới hạn này áp cả cho chú thích, nên ở Mixtral tr.12–13 (Phụ lục) chú thích “Figure 9” (108 ký tự), “Figure 10” (96 ký tự) bị giữ nguyên chữ gốc; sau khi sửa, trên 10 bài bench chỉ hai đoạn đó thôi bị giữ nguyên, không đoạn nào mới bị giữ nguyên. Trang scan (OCR) thì theo nhãn layout (`ocr_refs`): khối `reference_content`, cộng khối chữ thường trông như một mục tài liệu (có năm hoặc `[12]`) trong phần References kéo dài từ trang trước — đo trên 10 bản scan so với bản gốc: không sót mục nào, không nuốt nhầm Phụ lục/chú thích hình.
- **↻ Dịch lại kèm yêu cầu**: khung nhập yêu cầu (gợi ý sẵn: sát nghĩa hơn, tự nhiên hơn, giữ thuật ngữ tiếng Anh…, và các yêu cầu gần đây); **chỉ lần này / nhớ cho trang này / nhớ cho cả bài** (trang có yêu cầu đã nhớ hiện nhãn ✎). Trang có yêu cầu riêng (đã nhớ) không bị gộp chung lượt gọi với trang khác. Yêu cầu được ghép vào **cuối** system prompt (phần đầu giống nhau ở mọi lượt nên server vẫn tái dùng KV cache), và không được phá luật JSON / độ dài / chỗ giữ công thức — nên “dịch chi tiết hơn” vẫn bị cắt cho vừa khung. **↻ Dịch lại đoạn** (thanh nổi khi bôi đen) chỉ gửi các đoạn đã chọn, giữ bản dịch phần còn lại. Google Translate / model chuyên dịch không làm theo yêu cầu được (ô yêu cầu bị khoá).
- **⬇ Tải PDF ▾** (26/09/2026, học từ bản `dual` của PDFMathTranslate): 📄 **Bản dịch**; 📖 **Song ngữ — hai bản cạnh nhau** (mỗi trang rộng gấp đôi: bản gốc trái, bản dịch phải); 📑 **Song ngữ — xen kẽ trang** (trang gốc rồi trang dịch, khổ giấy như bản gốc — hợp để in hoặc đọc trên máy tính bảng); 🖍 **Bản gốc có tô** (tắt đồng bộ tô vàng thì vệt tô ở bản gốc chỉ có trong file này). Mọi kiểu đều giữ vệt tô vàng và ghi chú (bản song ngữ có vệt tô của cả hai bên) và mục lục/bookmark của bài (bản xen kẽ trỏ về trang gốc). API: `GET /api/<id>/download?which=dual&layout=side|alt`.
- API: `POST /api/<id>/translate/<n>?blocks=1,3` (dịch lại một phần; body có `instruction`, `instruction_scope`, `skip_refs`), `POST /api/<id>/page/<n>/skip`, `POST /api/<id>/page/<n>/keep`, `GET /api/<id>/download?which=original&sync=false`.

## Dịch hàng loạt bằng dòng lệnh (26/09/2026)
`scripts/batch_translate.py` gọi app đang chạy (`./restart.sh start`), nên bài dịch xong nằm luôn trong **Tài liệu đã lưu** của giao diện (đọc, tô, trò chuyện như thường). Chạy lại cùng file thì chỉ dịch các trang còn thiếu: app nhận ra file đã tải lên, nên ngắt giữa chừng (Ctrl-C, mất mạng, hết lượt model free) cứ chạy lại lệnh cũ.
```bash
.venv/bin/python scripts/batch_translate.py ~/Papers/ --out ~/Papers/vi --format translated,dual-side
.venv/bin/python scripts/batch_translate.py --list-providers
.venv/bin/python scripts/batch_translate.py bai.pdf --provider tuf -j 2
.venv/bin/python scripts/batch_translate.py bai.pdf --provider
.venv/bin/python scripts/batch_translate.py bai.pdf --pages 1-5,8 --set temperature=0 --set skip_refs=false --force
```
- Nhận file PDF hoặc thư mục (mọi `*.pdf` ngay trong thư mục). File ra đặt tên theo file gốc: `_vi`, `_song_ngu`, `_song_ngu_xen_trang`, `_goc_to_vang` (`--format translated|dual-side|dual-alt|original`, mặc định cạnh file gốc).
- **Nhà cung cấp** lấy từ app — có sẵn (OpenRouter, OpenAI, Claude, DeepSeek) và của bạn (⚙️ Cấu hình → mục 7: TUF, Ollama, proxy ChatGPT…): `--provider <id|tên|số thứ tự>` đặt Base URL + model dịch của nhà cung cấp đó, `--provider` không kèm tên thì hiện danh sách để chọn, `--list-providers` để xem. Mặc định: **OpenRouter với model free** (như app). Không đọc được cấu hình đã chỉnh trong trình duyệt (lưu ở `localStorage`); phần còn lại chỉnh bằng `--model`, `--base-url`, `--translator`, `--config file.json` hoặc `--set trường=giá_trị` (ưu tiên tăng dần: nhà cung cấp < `--config` < `--set` < `--model`…); gõ nhầm tên trường thì báo lỗi. Dòng đầu in ra nhà cung cấp và model sẽ dùng. Không cần truyền key: app lấy key đã nhớ theo base URL.
- `-j 2`: hai lượt gọi dịch song song trong một bài (như ô “song song” trong giao diện); `--span`: số trang giữ chỗ cho một lượt gọi.

## API key
Key lưu **theo từng server**, nên đổi qua lại OpenRouter → Ollama → OpenRouter không phải gõ lại, và **chỉ trên máy chạy app** (`data/keys.json`, quyền `600`, ghi bằng file tạm rồi đổi tên nên mất điện giữa chừng không hỏng file). Gõ key vào ô là gửi lên server một lần (`POST /api/keys`) rồi ô tự xoá; trình duyệt không lưu key (từ 11/10/2026 — bản cũ giữ trong `localStorage`, lần mở đầu tiên app chuyển lên server rồi xoá khỏi trình duyệt), nên một lỗi XSS sau này cũng không đọc được key. Request gửi key rỗng thì server tự điền key đã nhớ của base URL đó.

Server **không bao giờ trả key đầy đủ** về giao diện, chỉ trả dạng che (`sk-o…9f2c`) để hiện vào ô nhập. Xoá một key: đăng nhập app trên trình duyệt, mở Console (DevTools) rồi chạy `fetch("/api/keys?slot=" + encodeURIComponent("<base_url>"), {method: "DELETE"})` (API cần phiên đăng nhập, curl trơn nhận 401).

Trò chuyện / lập thuật ngữ / căn vệt tô chạy trên **server riêng** thì chỉ dùng key của server đó; để trống base URL mới dùng chung server + key đang dịch. (Trước 25/09/2026, ô key để trống thì app lấy key của server dịch gửi sang — với cấu hình mặc định, key OpenRouter bị gửi tới proxy ChatGPT mỗi lần tô vàng.)

## Đăng nhập và vào app từ xa (11/10/2026)
- **Một mật khẩu**, đặt bằng `.venv/bin/python auth.py --set-password` trên máy chạy app (không có trang đặt mật khẩu trên web). Lưu băm `scrypt` trong `data/auth.json` (quyền `600`); phiên là token ngẫu nhiên, server chỉ giữ bản băm, giữ **30 ngày** và tự gia hạn khi dùng. Đổi mật khẩu = thu hồi mọi phiên; nút **Đăng xuất** ở cuối ⚙️ Cấu hình.
- **Chặn mặc định** (`auth.Guard`): chưa đăng nhập thì chỉ mở được trang đăng nhập — mọi route, file tĩnh, `/docs`, `/openapi.json` đều trả 401 (hay chuyển về trang đăng nhập), kể cả route thêm sau này. Sai mật khẩu 5 lần trong một phút thì khoá đăng nhập một phút.
- **Chặn trang lạ**: request đổi dữ liệu (POST/PUT/PATCH/DELETE) phải có `Origin` khớp đúng scheme://host:cổng của app (CSRF; dịch vụ khác cùng tên máy khác cổng cũng bị chặn); header `Host` phải nằm trong danh sách cho phép (chặn DNS rebinding) — mặc định `localhost`, `127.0.0.1`, thêm tên khác bằng biến môi trường `ALLOWED_HOSTS`.
- **Header bảo mật** trên mọi phản hồi: CSP chỉ cho script của chính app, không tải ảnh/font/kết nối nơi khác, không cho nhúng khung; `nosniff`.
- **Giới hạn**: file tải lên tối đa 200 MB và 2.000 trang (`MAX_UPLOAD`, `MAX_PAGES` trong `app.py`; không có `Content-Length` — vd. HTTP/2 qua proxy — thì đếm từng mẩu lúc nhận), body đăng nhập tối đa 4 KB, phân tích PDF trong luồng riêng; ảnh trang `zoom` trong 0,5–4; một câu trả lời trò chuyện tối đa 8.192 token.
- **Trên server TUF**: app chỉ nghe `127.0.0.1:8000`; máy trong tailnet (Mac, iPhone) vào bằng **https://<máy>.<tailnet>.ts.net:8443** qua `tailscale serve` (HTTPS, chỉ trong tailnet, **không** bật Funnel — app còn tự từ chối mọi request có header `Tailscale-Funnel-Request`, phòng khi lỡ bật). Unit systemd đặt `HOST=127.0.0.1 ALLOWED_HOSTS=<máy>.<tailnet>.ts.net`; bật lối vào:
  ```
  tailscale serve --bg --https=8443 http://127.0.0.1:8000
  ```
- `scripts/batch_translate.py` đăng nhập bằng mật khẩu ở biến môi trường `VIETPAPER_PASSWORD`, không có thì hỏi khi chạy.
- Threat model và các chốt chặn: `.agent/threat-model.md`; kiểm bằng `scripts/test_security.py`.

## Font bản dịch
Bản dịch tự dùng font **cùng loại với chữ gốc** (có chân / không chân / đơn cách), giữ đậm-nghiêng. Chế độ "auto" chọn font có **độ rộng chữ gần font gốc nhất** (đo từ PDF): bài LaTeX dùng Times (0,44 em/chữ) → Times New Roman (0,44), thay vì Source Serif 4 (0,51) làm chữ phải co. Font nhúng được cắt chỉ còn ký tự đã dùng (MuPDF, không cần cài thêm) nên PDF tải về thường nhỏ hơn cả bản gốc.
- Có sẵn trên macOS: Times New Roman, Georgia, Arial, Courier New.
- Font đẹp hơn (OFL, đủ dấu tiếng Việt): Source Serif 4, Noto Serif, Be Vietnam Pro, Noto Sans →
  `.venv/bin/python scripts/download_fonts.py` (tải vào `fonts/`, khoảng 6MB), khởi động lại app rồi chọn trong ⚙️ Cấu hình.

## Kiểm tra nhanh sau khi sửa code
```
.venv/bin/python scripts/smoke_features.py    # ~3 phút, KHÔNG cần server LLM
.venv/bin/python scripts/test_security.py     # ~3 giây: đăng nhập, chặn route, Origin, Host, header, giới hạn
```
Script chạy app trong cùng tiến trình dùng `A.test_client()` / `A.test_async_client()` (Host localhost, phiên chỉ sống trong bộ nhớ, không cần mật khẩu); `TestClient(A.app)` trơn bị chặn như mọi request chưa đăng nhập.
Thay hàm gọi LLM bằng hàm giả rồi chạy thật cả luồng trên `data/bert.pdf`: gộp trang, bảng thuật ngữ (lập → sửa → lưu → vào đúng prompt), lớp chữ hai bản khớp số hiệu đoạn, tô vàng → annotation trong PDF tải về, ngữ cảnh gửi cho phần trò chuyện, nhớ API key, khôi phục sau khi tải lại trang, và lưu xuống đĩa rồi mở lại như sau khi khởi động lại app (xoá bộ nhớ → state, ảnh trang dịch, số hiệu đoạn, thuật ngữ, trò chuyện giống hệt; tải lên lại cùng file ra cùng id; xoá tài liệu). Dùng thư mục lưu tạm, không đụng `data/docs/`. In ✓/✗ từng mục và trả mã lỗi ≠ 0 nếu có mục sai.

## Bench bố cục PDF có lớp chữ
10 bài báo LLM ngắn trên arXiv (139 trang) trong `data/`: Attention, BERT, RoBERTa, RAG, RoFormer, GQA, FlashAttention-2, Mistral 7B, Mixtral, DataSentinel.
```
.venv/bin/python scripts/bench_text.py            # ~2 phút, không gọi LLM; --render: dựng cả bản dịch giả; --download: tải lại bài thiếu
```
Đếm các lỗi trích chữ: ô bảng bị dính, khối in sai đậm/nghiêng (lệch bản gốc > 20% số chữ), hai dòng bị trộn, chữ vụn giữa các `{vN}`, mất/thừa gạch nối; kết quả lưu ở `bench/results/` và tự so với lần chạy trước. Kết quả 19/09/2026:

| | Ô bảng dính | Sai đậm/nghiêng | Hàng trộn | Chữ vụn | Mất gạch nối | Lỗi dựng |
|---|---:|---:|---:|---:|---:|---:|
| Trước khi sửa | 127 | 199 | 21 | 6 | 71 | 0 |
| Sau khi sửa | 1* | 0 | 0 | 0 | 0 | 0 |

\* Ô chứa chuỗi tấn công vô nghĩa căn đều hai bên (DataSentinel tr. 16): khoảng cách giữa các từ rộng hơn một cỡ chữ nên thước đo tưởng là hai cột.

`--render` đo thêm bố cục: cỡ chữ bản dịch so với bản gốc, phần chữ bị co quá 15%, khối bị nội dung khối khác trượt vào, dung lượng PDF tải về. Trang có bản dịch thật trong `bench/translations/` (tạo bằng `scripts/bench_llm.py`, gọi LLM thật) thì dựng bằng bản dịch đó, còn lại in lại chữ gốc. Kết quả 19/09/2026 trên 4 trang dịch thật bằng qwen3.5:

| | Cỡ chữ so với gốc | Chữ bị co > 15% | Khối lệch khung | PDF 10 bài (gốc 13,2 MB) |
|---|---:|---:|---:|---:|
| Trước (Source Serif, giãn dòng 1,15, cỡ nhóm = phân vị 25%) | 86,5% | 21,5% | — | 19,4 MB |
| Sau | 97,6% | 3,3% | 0 | 13,7 MB |

## Bench OCR (tìm lệnh chạy tốt nhất cho model OCR)
`scripts/bench_ocr.py` chỉ cần `pymupdf`, chạy được ngay trên máy có server: tạo bản scan xấu từ PDF có lớp chữ, cho model đọc lại, chấm bằng độ khớp từ.
```
python3 scripts/bench_ocr.py --make-scan data/attention-is-all-you-need.pdf --out scan72.pdf --dpi 72 --jpeg 40
python3 scripts/bench_ocr.py --scan scan72.pdf --truth data/attention-is-all-you-need.pdf --pages 3-6 \
    --base-url http://127.0.0.1:8080/v1 --model 9b-vision --mode page --csv ocr.csv
```
`--mode region` cắt từng vùng chữ như app vẫn làm. Hướng dẫn đầy đủ cho người (hoặc AI) chỉnh server: [docs/ocr-tuf.md](docs/ocr-tuf.md) — hợp đồng gọi OCR, prompt từng model, tham số llama-server nên quét, mốc đã đo.

## Bench pipeline OCR (PDF scan, RapidOCR + PP-DocLayout)
`scripts/bench_ocr_pipeline.py` chạy đúng hàm OCR của app trên bản scan của 10 bài bench (`samples/*-scan.pdf`, tạo bằng `scripts/make_scans.py`) và so với **đáp án** là các đoạn app trích từ lớp chữ của bản gốc (`data/*.pdf`): từ đọc đúng, đoạn khớp 1–1 hay bị gộp/chia/sót/thừa, cỡ chữ, nhãn layout, chữ đậm. Không gọi LLM; dòng OCR thô được cache theo mã nguồn hàm (`bench/ocr/cache/`) nên sửa phần gom đoạn chạy lại mất vài giây.
```
.venv/bin/python scripts/make_scans.py                 # một lần: 10 bản scan vào samples/
.venv/bin/python scripts/bench_ocr_pipeline.py -v      # lần đầu ~10 phút (OCR 139 trang), sau đó vài giây
git show HEAD:app.py > app_before.py && .venv/bin/python scripts/bench_ocr_pipeline.py --app app_before.py --tag before
```
Kết quả 26/09/2026 (139 trang, 2.725 đoạn đáp án; M2, `--jobs 1` cho cột tốc độ):

| | Trước | Sau |
|---|---:|---:|
| Từ OCR đúng: phủ / chính xác | 98,45 / 96,13% | 99,30 / 96,88% |
| Đoạn khớp 1–1 với bản gốc | 76,7% | **97,3%** |
| Đoạn bị gộp / chia / sót / thừa | 535 / 16 / 85 / 39 | 33 / 31 / 9 / 69 |
| Cỡ chữ lệch trung bình | 16,9% | **5,9%** |
| Nhãn layout đúng | 78,5% | 99,8% |
| Khối sai đậm/thường | 329 | 210 |
| Giây OCR/trang | 4,86 | 4,70 |

Đã sửa:
- **Gom đoạn dùng chung code với PDF có lớp chữ** (`layout_groups`, `text_block`): dòng OCR được dựng thành "dòng giả" (span, vị trí từng ký tự) rồi đi qua `table_cells`, `algorithm_lines`, `region_paragraphs` như chữ thật. Trước đây OCR có bộ gom riêng: dòng ngoài vùng layout và cả bảng được gộp theo khoảng cách → 496/535 lỗi gộp là ô bảng dính nhiều hàng/cột (`MRPC 3.5k`); thuật toán thành một đoạn văn xuôi.
- **Vị trí từng ký tự** (`return_single_char_box` của RapidOCR, suy từ bước CTC, gần như không tốn thêm): khung dòng của bước dò chữ rộng hơn chữ thật 0,1–0,6 em nên hai ô tiêu đề bảng cách nhau một khoảng nhỏ bị coi là một (`Average CNN`). Khung khối sát chữ để gom/đặt chữ; khung dò chữ (rộng hơn) vẫn dùng để phủ nền khi xoá chữ gốc (`cover`) và để VLM cắt vùng đọc lại.
- **Cỡ chữ**: chiều cao lấy theo khung **xoay** của dòng thay vì khung thẳng trục (trang scan nghiêng thì khung thẳng trục cao thêm: sai số từng dòng 9,7 → 6,5%); khung dò chữ gần như có chiều cao tối thiểu (chữ 6pt → khung 11pt) nên trộn thêm cỡ ước từ **bề rộng** dòng so với bề rộng của chính chuỗi chữ đó in bằng Times (trung bình nhân, trọng số 0,7): 4,2% mỗi dòng, chữ ≤ 7pt 36 → 19%.
- **Chữ đậm** theo độ dày nét (số điểm mực / số lần chuyển trắng→mực, so với dòng thường của trang), quyết định theo cả khối; bỏ qua ô bảng (chữ 5–6pt nét tương đối dày, nhầm 1/3). Tiêu đề mục in đậm lại (88% tiêu đề mục trong đáp án là chữ đậm). 27 "nhầm" còn lại là của đáp án: font `NimbusRomNo9L-Medi` của Attention là chữ đậm mà bộ trích lớp chữ không nhận ra.
- Chữ OCR không bao giờ thành `{vN}` (không có công thức vector để chép lại); thêm dấu cách sau số mục OCR hay bỏ (`2Background`); dòng mã giả chỉ có từ khoá (`13: end for`) không dịch; chú thích cuối trang chỉ có link (`1https://…`) không dịch; không chạy bộ phân loại chữ ngược (`use_cls`); đưa thẳng mảng ảnh cho RapidOCR thay vì PNG (−0,3 s/trang).
- **Gộp trang scan vào một lượt gọi LLM** khi OCR bằng RapidOCR (~3–5 s/trang, so với 20–40 s một lượt gọi): trước đây trang phải OCR luôn dừng nhóm vì mốc 30–70 s/trang của Vision LLM.

Đã thử, không giữ: model nhận dạng `PP-OCRv6 tiny` nhanh gấp 3,5 lần (0,85 s/trang) nhưng đọc sai thêm ~1% từ; chạy tiny rồi đọc lại dòng kém tin cậy bằng model small chỉ gỡ được một nửa; CoreML (6,4 s/trang), đổi số luồng/lô nhận dạng đều không nhanh hơn. ~90% thời gian OCR là bước nhận dạng, chỉ 2,7% khối lượng đó rơi vào vùng hình/công thức bị bỏ nên không đáng tách riêng. Phần "thừa" còn lại phần lớn là chữ trong ảnh của bản gốc (bảng ảnh Mixtral tr.6, ảnh chụp màn hình Mistral tr.7) — OCR đọc được là đúng; nhiều lỗi "chia" là do đáp án gộp cả hàng tiêu đề bảng thành một dòng PDF.

**Bảng thuật ngữ và tên bài của PDF scan** (26/09/2026): trước đây chỉ đọc lớp chữ nên bản scan ra bảng rỗng (`[glossary] 0 thuật ngữ`) và không có tên bài. Nay lấy từ khối OCR (xem *Thuật ngữ & độ dài*). Đo bằng `scripts/bench_glossary.py --scan` (cùng đáp án 6 bài, dùng cache OCR của bench này) so với chạy thường trên bản PDF gốc:

| đúng / phủ % (dev · test) | PDF gốc | Bản scan |
|---|---:|---:|
| 40 ứng viên đầu (không LLM) | 83,8/73,4 · 80,0/81,0 | 84,2/76,0 · 80,0/81,0 |
| Độ phủ của 80 ứng viên | 88,2 · 95,6 | 88,2 · 95,6 |
| Bảng sau LLM (`4b` TUF) | 90,8/64,8 · 86,3/76,5 | 89,9/63,5 · 86,3/78,5 |

Tên bài đúng cả 10 bài (chữ in hoa nhỏ đọc thành in hoa: `FLASHATTENTION-2`). OCR tới hết trang sau *References* thay vì cả bài cho cùng kết quả mà bớt ~1/3 số trang: `gqa-scan.pdf` 6/7 trang, 22 giây trước lượt gọi LLM lập bảng (trước đây ~4 giây OCR trang đầu), các trang đó dịch sau không phải OCR lại. Riêng FlashAttention-2 bản scan, `4b` giữ nguyên tiếng Anh mọi cụm mô tả (xếp 6 cụm như `high bandwidth memory` là tên riêng, các cụm khác như `causal mask` thì "dịch" bằng chính chữ tiếng Anh) — đổi tên bài sang in hoa toàn bộ hay viết thường cũng hết, nên là độ nhạy của model nhỏ với prompt; nay 6 hàng xếp nhầm là tên riêng được hỏi lại cách dịch (xem *Kiểm lại câu trả lời của LLM*), phần còn lại là lựa chọn của model. ling-flash dịch đủ (`causal mask` → mặt nạ nhân quả, `sequence length` → chiều dài chuỗi). Model mặc định ling-flash (OpenRouter free): PDF gốc 86,9/69,7 · 80,5/81,0, bản scan 86,6/72,1 · 80,5/81,0.

Cũng cho PDF scan: **không dịch Tài liệu tham khảo** theo nhãn layout (`ocr_refs`, xem *Đọc, trò chuyện…*) và **mục lục bài cho phần trò chuyện** từ khối `paragraph_title` (`doc_outline(doc, ocr=…)`, tự cập nhật khi OCR thêm trang).

Giới hạn còn lại: trang đầu của bản scan phải chờ OCR tới hết References trước khi lập bảng (bài 7 trang ~25 giây, 20 trang ~1 phút; tiến độ hiện trên giao diện); OCR không cho biết chữ nghiêng (chỉ dùng trong thử nghiệm xếp hạng, app không dùng) và đọc chữ in hoa nhỏ thành in hoa (`FLASHATTENTION`) — đúng như hình in, không đoán lại được cách viết thường; `4b` vẫn có khi giữ nguyên tiếng Anh cụm nên dịch (`sequence length`) — model mặc định (ling-flash) dịch đúng.

## Cách hoạt động
1. PyMuPDF trích từng ký tự kèm toạ độ, cỡ chữ, màu, đậm/nghiêng; PP-DocLayoutV3 gom dòng thành đoạn theo vùng bố cục và bỏ qua công thức đứng riêng, chữ trong hình, số trang. Số dòng ở lề của bản nộp hội nghị (ICLR/ACL: `000`, `001`… cả hai lề; NeurIPS: số lề trái khớp từng dòng) được layout nhận là chữ bên lề: không lẫn vào đoạn, không bị dịch, giữ nguyên chỗ trên bản dịch (kiểm 26/09/2026 trên bản thử chèn số dòng vào Attention/BERT/RoFormer — bản trên arXiv thường đã bỏ số dòng; smoke test mục 16). Chữ đậm/nghiêng giữa đoạn (tiêu đề đầu đoạn, tên tác giả, tên hội nghị trong tài liệu tham khảo) được bọc `<b>`/`<i>` và giữ trong bản dịch. Bảng dịch từng ô (ô nhiều dòng được nối lại thành câu, ô toàn số giữ nguyên); vùng thuật toán/khối code dịch **từng dòng** (giữ số dòng, thụt lề, chú thích `// …` căn phải; dòng ↪ và phần xuống dòng của một bước được nối vào dòng trên) và chỉ dịch dòng có văn xuôi (tiêu đề, `Parameters: …`, chú thích, câu trong prompt) — dòng toàn từ khoá/tên biến/JSON giữ nguyên vì dịch ra là hỏng code (`action` → "hành động"); khối có nhiều công thức hơn từ (nhãn toán trong sơ đồ) không dịch. Gạch nối cuối dòng: bỏ gạch ngắt từ (trans-lation), giữ gạch của từ ghép (LLaMA3-8B, pre-trained) dựa vào cách chính bài báo viết từ đó ở chỗ khác. Bấm "Dịch lại" mà không đổi cấu hình OCR thì dùng lại kết quả trích/OCR cũ, chỉ dịch lại.
2. Công thức nằm giữa câu (font toán CMMI/CMSY/CMEX…, chỉ số trên/dưới, ký hiệu không có mã Unicode như Σ) được thay bằng `{v1}`, `{v2}`… trước khi dịch; bản dịch làm mất `{vN}` bị loại (dịch lại / Google / giữ nguyên bản gốc). Dấu mũ rời của `\hat{y}` (glyph `ˆ` in bằng font chữ thường) thuộc về công thức đứng sau nó; tên riêng kiểu `Sch¨olkopf` vẫn là chữ. Đường chân chữ của hàng tính theo số đông ký tự, không theo span đầu tiên (span bắt đầu bằng chỉ số trên làm cả hàng lệch 3,6pt — RoFormer tr.2), và chữ chính của dòng bỏ qua chỉ số in bằng font chữ thường (`model` 5pt của d_model — trước đây làm chỉ số `i` của `W_i^Q` thành một hàng riêng, vẽ đè xuống dòng dưới, Attention tr.5). Vùng cắt công thức không xuống quá đỉnh công thức dòng dưới (khung ký tự CMSY xuống gần 1 em dưới chân chữ → bóng `ŷ<i` của dòng dưới, DataSentinel tr.3).
3. Gửi theo lô JSON cho LLM; đoạn nào lỗi thì dịch lại riêng. Bản dịch **lẫn chữ hệ khác** mà bản gốc không có (Hán/Kana/Hangul, Kirin, Ả Rập, Thái… — model đa ngữ đôi khi chèn `tính tự回归` giữa câu) bị loại như bản mất công thức: dịch lại riêng, rồi Google; dấu câu toàn khổ (`，` `（` `。`) thì đổi về dấu thường. Chữ Hy Lạp không tính (ký hiệu toán). Đo 26/09/2026 trên Attention tr.3–5 bằng `dots-3-note-preview:free`: 1 đoạn lẫn `投影` hai lần liền → Google dịch đoạn đó, PDF không còn chữ Hán. Bản dịch **còn nguyên nửa câu tiếng Anh** cũng bị loại như vậy: có cụm ≥ 5 từ liền nhau chép y bản gốc chứa ≥ 2 từ chức năng (the, in, as, we…) — `Firstly, we separate them with a special token…` — hoặc đoạn ngắn trả về y nguyên mà có từ chức năng (tiêu đề `Background and Related Work`; giống "same-text fallback" của BabelDOC). Thuật ngữ, tên riêng, tiêu đề chỉ gồm thuật ngữ (`Multi-Head Attention`), cụm trong ngoặc kép ở bản gốc (câu lệnh tấn công, prompt mẫu) và ô bảng không bị loại. Đo bằng `scripts/bench_english.py` (Attention tr.3–5, BERT tr.3–4, RoFormer tr.2; đếm từ tiếng Anh thông dụng chép từ bản gốc mà không thuộc bảng thuật ngữ, bảng thuật ngữ đóng băng): `dots-3-note-preview:free` 2,56 / 2,48 / 1,74 từ sót mỗi 100 từ qua ba lần chạy — dao động giữa các lần lớn hơn tác dụng của mọi luật prompt đã thử (thêm luật "dịch mọi từ thường": 2,48, không khác), nên không đổi prompt; `inclusionai/ling-3.0-flash-fin:free` 0,87 (và nhanh gấp 3) — bộ lọc bắt 3 đoạn còn nguyên nửa câu, dịch lại đều đạt; phần còn lại chủ yếu là tên người (howard, devlin) mà thước đo tính nhầm. `stealth/space-bunny-alpha` (03/10/2026): 1,29, 113 s — sót chủ yếu values/queries/keys (Q, K, V của attention) và tên người. `inclusionai/ling-3.0-flash-sante:free` (03/10/2026): 1,41, 72 s — bộ lọc bắt 2 đoạn lẫn chữ Hán (有价值, 基准) và 3 đoạn còn nửa câu tiếng Anh, dịch lại đều đạt. `inclusionai/ling-3.1-flash` (03/10/2026, mặc định mới): **0,61**, 74 s, không đoạn nào bị bộ lọc loại. Chọn model quan trọng hơn prompt. Đoạn là mảnh câu bị ngắt sang cột/trang khác được gửi kèm phần câu trước/sau làm ngữ cảnh (chỉ để hiểu, không dịch).
4. Xoá chữ gốc trong khung (giữ hình/đường kẻ và **màu nền**: không phủ ô trắng, nên chữ trên hộp tô màu, hàng bảng tô xám vẫn giữ nền; vùng xoá lùi khỏi dòng của khối bên cạnh mà khung chạm vào) và chèn bản dịch vào đúng khung với giãn dòng như bản gốc. Bản dịch dài hơn thì được lấn vào khoảng trống sẵn có (xuống dưới tối đa một dòng, ô bảng/khối một dòng sang phải) trước khi phải co chữ; các đoạn cùng kiểu chữ trên trang in cùng một cỡ (trung vị). Mỗi `{vN}` được thay bằng chính công thức gốc (chép dạng vector từ trang gốc) nên chỉ số, căn thức, phân số giữ nguyên. Font khai báo phần dưới chân chữ ≈ 0 (NewTXMI của gói newtx) thì vùng chép được nới xuống theo nét mực, không cụt đuôi `y`, dấu phẩy.
5. Giao diện hiển thị bản gốc bên trái – bản dịch bên phải; tải về PDF đã dịch.
6. Mỗi ảnh trang được phủ một lớp chữ trong suốt (lấy lại từ lớp chữ PDF của chính trang đó) để bôi đen, tô vàng đồng bộ hai bản và hỏi LLM về đoạn đang đọc.

Giới hạn: trang scan (OCR) chưa giữ được công thức giữa câu — chữ lấy từ OCR nên công thức thành chữ thường hoặc bị bỏ.
