"""VietPaper: dịch bài báo PDF sang tiếng Việt, giữ nguyên bố cục.

- Dịch: mọi API tương thích OpenAI (OpenRouter, OpenAI, Claude, DeepSeek, Ollama, llama.cpp…) + Google Translate (fallback).
- OCR cho PDF scan: PP-DocLayoutV3 chia vùng + RapidOCR PP-OCR (local, mặc định), Tesseract (local),
  OCR.space (API miễn phí), vision LLM (PaddleOCR-VL / GLM-OCR đọc từng vùng, Unlimited-OCR cả trang).
"""
import asyncio
import atexit
import base64
import collections
import contextvars
import hashlib
import heapq
import io
import json
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import quote
from typing import NamedTuple

import httpx
import pymupdf
from pymupdf import mupdf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import chats as C
import notes as N
import store

BASE = Path(__file__).parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

app = FastAPI(title="VietPaper")

# doc_id -> {"id", "src": Path, "out": pymupdf.Document, "done": set[int], "lock": asyncio.Lock, "name": str, …}
# Bộ đệm của tài liệu đang mở; bản lưu thật nằm trên đĩa (store.py, data/docs/{id}/) nên xoá DOCS không mất gì.
DOCS: dict[str, dict] = {}


OCRSPACE_DEMO = "helloworld"


class Config(BaseModel):
    # LLM
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    model: str = "google/gemini-2.5-flash"
    temperature: float = 0.2
    glossary: str = ""
    glossary_auto: bool = True     # tự lập bảng thuật ngữ một lần cho mỗi tài liệu (một lượt gọi LLM)
    disable_thinking: bool = True  # gửi reasoning_effort="none" tới server local (Qwen3, DeepSeek-R1… dịch nhanh hơn nhiều)
    num_ctx: int = 0               # ngữ cảnh model (token) khi dùng Ollama; 0 = tự chọn theo số token của tài liệu
    # Font bản dịch (tên trong FONTS); "auto" = font tốt nhất đang có
    font_serif: str = "auto"
    font_sans: str = "auto"
    font_mono: str = "auto"
    # Dịch
    translator: str = "llm"        # "llm" (JSON theo lô) | "mt" (model chuyên dịch, từng đoạn) | "google"
    google_fallback: bool = True   # dùng Google Translate khi LLM lỗi / bỏ sót đoạn
    length_ratio: float = 1.3      # bản dịch tối đa ≈ ratio × độ dài bản gốc
    page_span: int = 0             # số trang gộp vào một lượt gọi LLM; 0 = tự động theo ngữ cảnh còn trống
    skip_refs: bool = True         # không dịch phần Tài liệu tham khảo (giữ nguyên chữ gốc, đỡ token)
    instruction: str = ""          # yêu cầu thêm của người đọc cho lượt dịch (lại) này, ghép vào CUỐI system prompt
    instruction_scope: str = "once"  # "once" | "page" (nhớ cho các trang này) | "doc" (nhớ cho cả bài)
    # OCR
    ocr_mode: str = "auto"         # "auto" (trang không có chữ) | "force" | "off"
    ocr_engine: str = "rapidocr"   # "rapidocr" | "tesseract" | "ocrspace" | "vlm" | "custom"
    ocr_lang: str = "eng"
    ocr_api_key: str = OCRSPACE_DEMO  # key demo của OCR.space, nên đăng ký key riêng (miễn phí)
    # OCR bằng vision LLM (OpenAI-compatible): PP-DocLayout + RapidOCR định vị đoạn, VLM đọc chữ
    ocr_vlm_base_url: str = "http://100.64.0.1:8080/v1"   # server TUF (PaddleOCR-VL trên llama.cpp)
    ocr_vlm_api_key: str = ""
    ocr_vlm_model: str = "paddleocr-vl"
    ocr_vlm_concurrency: int = 1   # số vùng gửi cùng lúc; llama-server chạy -np 1 (vd. TUF) nên để 1, Ollama/API cloud 4
    # OCR qua endpoint HTTP tuỳ chỉnh
    ocr_custom_url: str = ""
    ocr_custom_api_key: str = ""
    # Trò chuyện về tài liệu (để trống = dùng đúng cấu hình LLM dịch ở trên)
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    # Lập bảng thuật ngữ (để trống = dùng đúng cấu hình LLM dịch). Chỉ một lượt gọi cho mỗi tài liệu nên đáng dùng model
    # mạnh hơn model dịch: bench_glossary 21/09/2026, Nemotron Ultra (OpenRouter, free) lọc/dịch tốt hơn hẳn 4b local.
    gloss_base_url: str = ""
    gloss_api_key: str = ""
    gloss_model: str = ""
    # Căn vệt tô vàng sang bản kia bằng LLM (để trống = dùng đúng cấu hình LLM dịch). Bật/tắt nằm ở giao diện.
    align_base_url: str = ""
    align_api_key: str = ""
    align_model: str = ""
    # Model xem ảnh trang khi trò chuyện (câu hỏi về hình, bảng, công thức): để trống model = không gửi ảnh.
    # Proxy ChatGPT không nhận ảnh (25/09/2026) → mặc định giao diện là OpenRouter dots-studio/dots-3-note-preview:free
    # (nex-agi/nex-n2.5-pro:free chọn trước đó bị OpenRouter gỡ cùng ngày).
    see_base_url: str = ""
    see_api_key: str = ""
    see_model: str = ""
    # Công thức → LaTeX khi trò chuyện (cắt ảnh từng công thức, model đọc ảnh chép ra LaTeX, lưu lại dùng mãi):
    # "ocr" = model OCR ở mục OCR (mặc định PaddleOCR-VL local), "see" = model xem ảnh ở trên, "off" = chỉ chép glyph
    formula_ocr: str = "ocr"


SYSTEM_PROMPT = """You are an expert academic translator. Translate research paper text (usually English or Chinese) into natural, precise Vietnamese.
Rules:
- The translation is placed back into the ORIGINAL layout box: each output "text" must not exceed that item's "max_len" characters. Write natural, grammatically complete Vietnamese — shorten by choosing compact wording, never by dropping subjects, prepositions or connectors. Never add explanations or parenthetical glosses.
- Items with "tight": true are narrow table cells: use the shortest correct wording, or keep the English term/abbreviation.
- Keep technical terms commonly used in English (e.g. Transformer, dropout, embedding) when that is the Vietnamese academic norm. When a Glossary is given, follow it exactly for every term it lists.
- The authors' "we" is "chúng tôi" (not "chúng ta").
- Keep math, variables, citations like [12] or (Smith et al., 2020), URLs, numbers and code unchanged.
- Placeholders like {v1}, {v2} stand for formulas and symbols: keep every placeholder exactly once and unchanged, at the position where that formula belongs in the Vietnamese sentence.
- Tags <b>…</b> and <i>…</i> mark bold/italic words: keep every tag, wrapped around the Vietnamese words that translate the tagged words.
- Some items are fragments of a sentence split across columns or pages. Their optional "prev" (end of the preceding text) and "next" (start of the following text) are context only: use them to understand the sentence, but translate ONLY "text" and never copy words from "prev"/"next" into the output.
- Do not merge or split items.
Input is a JSON object {"items": [{"id": n, "max_len": m, "text": "...", "prev": "...", "next": "...", "tight": true}]} ("prev"/"next"/"tight" optional).
Output ONLY a JSON object {"items": [{"id": n, "text": "<Vietnamese>"}]} with the same ids."""

GLOSSARY_PROMPT = """You prepare the terminology list for translating ONE research paper into Vietnamese, so the whole
paper translates each term the same way. The candidates were picked automatically; many are NOT terms.
For every candidate decide "kind", reading "ctx" (a sentence of the paper where it appears):
- "name": a name or abbreviation that stays in English: model, method, dataset, benchmark, metric, hardware,
  software, library (Transformer, BERT, GQA, BLEU, A100, PyTorch, LLM). Leave "vi" empty.
- "term": a technical concept of this paper that must be worded the same everywhere
  (multi-head attention, target task, relative position, memory bandwidth).
- "other": NOT a term: ordinary words or phrases (base model, translation task, overhead, batch size, proportion),
  a fragment cut out of a longer phrase, a person's name, a math symbol, text from a table or figure. Leave "vi" empty.
- Variants of one term (plural, hyphenation, "positional"/"position", with or without a trailing generic noun such as
  "outer min" / "outer min problem"): keep only the most frequent one. An abbreviation and its long form are NOT
  variants: keep both.
- Keep at most {max_terms} as "name" or "term": the terms a reader meets again and again ("n" = how many times the
  candidate occurs in the paper) or that are central to its method. A generic phrase that happens to occur a few
  times (hidden states, beam size, weighted sum) is "other".
For "term", "vi" is the exact Vietnamese wording to use, as in the meaning of "ctx":
- Default to Vietnamese: descriptive terms MUST be translated ("injected task" → "nhiệm vụ bị tiêm",
  "target task" → "nhiệm vụ mục tiêu", "relative position" → "vị trí tương đối", "embedding" → "nhúng").
- Write the English term unchanged ONLY where Vietnamese papers keep it in English (attention, self-attention,
  multi-head attention, prompt injection, fine-tuning, pre-training, token, softmax, embedding layer names...).
- No explanations, no brackets.
Input {"paper": "<title>", "abstract": "<start of the abstract>", "items": [{"id": n, "term": "<candidate>", "n": <count>, "ctx": "..."}]}
Output ONLY {"items": [{"id": n, "kind": "name" | "term" | "other", "vi": "<Vietnamese wording or empty>"}]}
with every id."""

# Ứng viên gửi LLM, gợi ý thêm (không qua LLM) và số hàng tối đa của bảng. bench_glossary 21/09/2026 (6 bài có đáp
# án tay), 40 ứng viên: 4b 91/86% chính xác dev/test, độ phủ 65/77%, ~29 giây/bài; Nemotron Ultra 90/82%, 63/79%.
# Gửi nhiều hơn thì độ phủ lên nhưng đuôi danh sách là 57–88% rác và model lấp bảng bằng chúng (60: chính xác 88/83%,
# 150: 75–84%), cắt theo xác suất của mô hình xếp hạng học từ 4 bài cũng không hơn (scripts/fit_term_ranker.py).
# Vì vậy ứng viên hạng 41–80 không gửi LLM mà hiện thành "gợi ý khác" cho người dùng tự thêm.
GLOSSARY_FIXED, GLOSSARY_SUGGEST, GLOSSARY_ROWS = 40, 40, 40
GLOSSARY_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["id", "kind", "vi"], "properties": {
        "id": {"type": "integer"}, "kind": {"type": "string", "enum": ["name", "term", "other"]},
        "vi": {"type": "string"}}}}}}

SHORTEN_PROMPT = """You shorten Vietnamese translations of academic text so they fit a fixed layout box.
Rewrite each "text" more concisely in Vietnamese, keeping the full meaning, math, citations and numbers, within "max_len" characters.
Keep every placeholder like {v1} exactly once and unchanged, and every <b>…</b> / <i>…</i> tag pair.
Input {"items": [{"id": n, "max_len": m, "source": "<original text>", "text": "<Vietnamese too long>"}]}.
Output ONLY {"items": [{"id": n, "text": "<shorter Vietnamese>"}]}."""


CHAT_PROMPT = """Bạn là trợ giảng đọc bài báo khoa học cùng một người Việt. Bạn được cho nội dung (một phần) bài báo ở dạng chữ gốc kèm bản dịch tiếng Việt.
Cách trả lời:
- Viết bằng tiếng Việt, ngắn gọn, đi thẳng vào ý; liệt kê thì dùng gạch đầu dòng. Không mở bài, không nhắc lại câu hỏi.
- Bám vào nội dung được cho. Điều gì bài không nói mà bạn suy ra hoặc lấy từ kiến thức ngoài thì phải nói rõ là như vậy.
- Không bịa số liệu, không bịa trích dẫn, không bịa tên phương pháp.
- Giữ nguyên thuật ngữ tiếng Anh nếu Bảng thuật ngữ ghi giữ nguyên.
- Người dùng bôi đen một đoạn thì giải thích đúng đoạn đó: ý chính, các ký hiệu và công thức trong đoạn, và vai trò của đoạn trong mạch bài.
Định dạng (câu trả lời được hiển thị bằng Markdown + KaTeX):
- Dùng Markdown: **đậm** cho ý chính, gạch đầu dòng, bảng Markdown khi so sánh nhiều thứ.
- Công thức viết bằng LaTeX: giữa câu đặt trong $...$, công thức riêng một dòng đặt trong $$...$$. Ví dụ: $\\mathrm{softmax}(QK^\\top/\\sqrt{d_k})V$.
- Không bọc cả câu trả lời trong khối ``` — chỉ dùng ``` cho code thật."""


# Rút thuật ngữ. _STOP: từ chức năng, cắt cụm tại đây ("of" được nằm giữa cụm: mixture of experts).
# _EDGE: tính từ/động từ/danh từ chung của văn học thuật, không đứng đầu hay cuối cụm và không là thuật ngữ một mình
# ("different models", "proposed method"). _WEAK: danh từ chung chỉ được nằm trong cụm ("language model",
# "target data", "position information"), một mình thì không phải thuật ngữ.
_STOP = set("""a an the and or nor but if of in on at to for from by with without as is are was were be been being
am this that these those it its we our ours us they their them he she his her i you your which who whom whose what
when where while how why than then there here also can could may might must should would will shall do does did
done not no only such both each every all any some many much more most less least few other another same very so
thus hence however therefore i.e e.g eg ie et al etc via into onto over under between among per vs versus whether
since because although though after before during until within upon about above below up down out off has have had
having one two three four five six seven eight nine ten first second third let just even still yet again always
often never already respectively namely further furthermore moreover instead rather either neither whereas through
throughout across along around toward towards against beyond besides except s""".split())
_EDGE = set("""different same single given proposed following previous existing standard various several new novel
better best good worse simple original overall total final main important significant similar specific general
additional full entire whole recent prior current common typical respective corresponding particular certain possible
able likely large larger largest small smaller smallest high higher highest low lower lowest long longer short
shorter fast faster slow slower strong stronger weak weaker effective efficient efficiently significantly slightly
show shows shown showing use uses used using propose proposes obtain obtained achieve achieves achieved perform
performs performed improve improves improved outperform outperforms compare compared comparing evaluate evaluated
consider considered note see find found observe observed need needs needed make makes made take takes taken provide
provides provided allow allows allowed require requires required apply applied include includes including based
result results method methods approach approaches paper papers work works study studies experiment experiments
experimental setting settings section sections table tables figure figures fig appendix equation equations eq
eqs example examples case cases number numbers amount performance evaluation analysis effect effects impact
improvement improvements baseline baselines set sets time times way ways part order fact point form level type
types kind term terms detail details step steps report reported describe described define defined denote denotes
existing prior related detailed average respectively specifically directly roughly approximately particularly
highly well much case demonstrate demonstrates suggest suggests explore issue issues
question questions answer goal aim purpose idea ideas contribution contributions limitation limitations
future past present previous prior like unlike well-known widely known widely-used learn train reduce enable encode
incorporate load divide write read predict generate produce capture handle contain leverage employ adopt introduce
discuss investigate analyze apply extend modify replace remove add combine return lead yield remain become keep
give run operate fit help ensure support involve depend focus attempt try want call refer correspond express
represent consist theoretical empirical identical addition alternative formulation relation property trade-off
symbol aspect approach manner respect regard terms""".split())
_WEAK = set("""model models data information language learning system systems task tasks input inputs output outputs
function functions problem problems process result value values vector vectors matrix matrices parameter
parameters feature features representation representations sample samples dataset datasets training test testing
network networks algorithm algorithms computation cost costs quality accuracy score scores error errors loss rate
size length dimension dimensions range scale version setup framework technique techniques strategy strategies
component components structure structures mechanism mechanisms application applications domain domains space
product products sequence sequences memory speed element elements object objects operation operations weight
weights evidence content signal module modules unit units instance instances pair pairs group groups block blocks
item items text texts word words sentence sentences document documents experiment state states attack attacks
research authors author tool tools solution solutions interface user users human humans position layer
maximum minimum architecture dependency translation complexity modeling implementation variant variants""".split())
_CAPS_STOP = {"TABLE", "FIGURE", "FIG", "URL", "II", "III", "IV", "VI", "VII", "VIII", "IX", "NOTE", "ABSTRACT",
              "INTRODUCTION", "CONCLUSION", "REFERENCES", "USA", "PDF", "HTTP", "HTTPS", "WWW", "OK", "ID", "IEEE",
              "ACM", "ACL", "NIPS", "ICLR", "ICML", "EMNLP", "NAACL", "AAAI", "CVPR", "CORR", "ARXIV"}


def is_self_hosted(url: str) -> bool:
    """localhost, IP LAN/Tailscale (10.x, 192.168.x, 172.16-31.x, 100.64-127.x), *.local, *.ts.net."""
    host = (httpx.URL(url).host or "").lower()
    if host in ("localhost", "0.0.0.0") or host.endswith((".local", ".ts.net", ".lan")):
        return True
    m = re.fullmatch(r"(\d+)\.(\d+)\.\d+\.\d+", host)
    if not m:
        return False
    a, b = int(m[1]), int(m[2])
    return a in (10, 127) or (a, b) == (192, 168) or (a == 172 and 16 <= b <= 31) or (a == 100 and 64 <= b <= 127)


# Tham số tuỳ chọn mà một (url, model) từ chối — đã hỏi lại không có nó và được, nên lần sau khỏi gửi:
# - "temperature": model suy nghĩ của OpenAI chỉ nhận giá trị mặc định (đo 24/09/2026: gpt-6-luna qua proxy ChatGPT OAuth
#   trả 500 "Bad Request" khi có temperature, còn stream thì trả 200 rồi ngắt kết nối; gpt-5.6-terra thì nhận).
# - "reasoning": model trên OpenRouter bắt buộc suy nghĩ thì báo lỗi khi bị tắt.
_REFUSED: dict[tuple[str, str], set[str]] = {}


def refused_param(body: dict, status: int, text: str) -> str | None:
    """Lỗi này có phải do một tham số tuỳ chọn không: trả tên tham số nên bỏ để hỏi lại, hoặc None.
    Chỉ nhận lỗi có nhắc tới tham số đó (hay "Bad Request" trơn của proxy ChatGPT) — lỗi khác như vượt ngữ cảnh,
    429, sai key thì hỏi lại cũng vô ích, và không được làm mất temperature của những lượt dịch sau."""
    t = text.lower()
    if status not in (400, 422, 500):
        return None
    if "reasoning" in body and "reasoning" in t:
        return "reasoning"
    if "temperature" in body and ("temperature" in t or '"bad request"' in t):
        return "temperature"
    return None


def no_think(cfg: "Config", base_url: str, body: dict) -> dict:
    """Tắt chế độ suy nghĩ: server tự host (Ollama/llama.cpp/LM Studio) bằng reasoning_effort/chat_template_kwargs,
    OpenRouter bằng `reasoning` (phần lớn model free là model suy nghĩ: để nguyên thì phần nghĩ ăn hết trần token —
    đo 25/09/2026, Nemotron Ultra dịch một trang chỉ ra 4/12 đoạn). API cloud khác giữ nguyên tham số."""
    if cfg.disable_thinking and is_self_hosted(base_url):
        body["reasoning_effort"] = "none"                          # Ollama
        body["chat_template_kwargs"] = {"enable_thinking": False}  # llama.cpp / vLLM (Qwen3, Qwen3.5…)
    elif cfg.disable_thinking and "openrouter.ai" in base_url:
        body["reasoning"] = {"enabled": False}
    for k in _REFUSED.get((base_url, body.get("model", "")), ()):
        body.pop(k, None)
    return body


# ================================================================ API key đã dùng
# slot (base_url của server LLM, "ocrspace", hoặc URL endpoint OCR tuỳ chỉnh) → API key.
# Trình duyệt vẫn lưu key theo từng base_url trong localStorage; bản sao ở đây để xoá dữ liệu duyệt web hoặc mở app
# từ máy khác vẫn dịch được mà không phải gõ lại. KHÔNG bao giờ trả key đầy đủ ra UI (ai mở được app cũng đọc được).
KEYS_FILE = DATA / "keys.json"
_keys: dict[str, str] = {}


def load_keys() -> None:
    global _keys
    try:
        _keys = {k: v for k, v in json.loads(KEYS_FILE.read_text()).items() if isinstance(v, str) and v}
    except (OSError, ValueError):
        _keys = {}


def remember(slot: str, key: str) -> str:
    """Nhớ key vừa nhận cho slot đó, và trả lại key đã nhớ nếu lần này gửi rỗng."""
    slot = (slot or "").rstrip("/")
    if not slot:
        return key
    if key and key != _keys.get(slot):
        _keys[slot] = key
        try:
            KEYS_FILE.write_text(json.dumps(_keys, indent=1, ensure_ascii=False))
            KEYS_FILE.chmod(0o600)
        except OSError as e:  # noqa: BLE001 — không lưu được thì vẫn dịch bình thường
            print(f"[key] không lưu được {KEYS_FILE}: {e!r}")
    return key or _keys.get(slot, "")


# ================================================================ Thuật ngữ người dùng đã dạy
# Học từ bảng thuật ngữ người dùng lưu (PUT /api/<id>/glossary), dùng cho MỌI tài liệu sau:
#   blocked  thuật ngữ tự lập bị xoá (✕) → không gợi ý nữa (LLM không thấy ứng viên đó)
#   prefer   thuật ngữ người dùng sửa cách dịch / chuyển dịch↔giữ nguyên / tự thêm → dùng đúng cách đó, và thêm vào bảng
#            của mọi bài có thuật ngữ này dù LLM không chọn
# Khoá = _term_key (models = model). Bỏ tích "Dùng" chỉ có tác dụng trong bài đó, không học.
USER_TERMS_FILE = DATA / "user_terms.json"
_uterms: dict[str, dict] = {}


def user_terms() -> dict[str, dict]:
    if not _uterms:
        try:
            _uterms.update(json.loads(USER_TERMS_FILE.read_text()))
        except (OSError, ValueError):
            pass
        _uterms.setdefault("blocked", {})
        _uterms.setdefault("prefer", {})
    return _uterms


def save_user_terms() -> None:
    try:
        USER_TERMS_FILE.write_text(json.dumps(user_terms(), ensure_ascii=False, indent=1))
    except OSError as e:
        print(f"[glossary] không lưu được {USER_TERMS_FILE}: {e!r}")


def learn_terms(old: list[dict], new: list[dict]) -> None:
    """So bảng cũ (app lập / lần lưu trước) với bảng người dùng vừa lưu → nhớ thuật ngữ bị xoá và cách dịch được sửa."""
    ut = user_terms()
    key = lambda r: _term_key(r["term"].split())
    before, after = {key(r): r for r in old if r["term"].strip()}, {key(r): r for r in new if r["term"].strip()}
    for k in before.keys() - after.keys():
        ut["blocked"][k] = before[k]["term"]
        ut["prefer"].pop(k, None)
    for k, r in after.items():
        o = before.get(k)
        if o is None or o.get("keep") != r.get("keep") or (o.get("vi") or "").strip() != (r.get("vi") or "").strip():
            ut["prefer"][k] = {"term": r["term"].strip(), "vi": (r.get("vi") or "").strip(), "keep": bool(r.get("keep"))}
            ut["blocked"].pop(k, None)
    save_user_terms()


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "••••"


def resolve_keys(cfg: "Config") -> "Config":
    """Điền key đã nhớ vào các ô để trống và nhớ key mới. Gọi ở mọi endpoint nhận Config."""
    cfg.api_key = remember(cfg.base_url, cfg.api_key)
    cfg.ocr_vlm_api_key = remember(cfg.ocr_vlm_base_url, cfg.ocr_vlm_api_key)
    cfg.ocr_custom_api_key = remember(cfg.ocr_custom_url, cfg.ocr_custom_api_key)
    if cfg.ocr_api_key != OCRSPACE_DEMO:  # key demo dùng chung thì không cần nhớ
        cfg.ocr_api_key = remember("ocrspace", cfg.ocr_api_key) or OCRSPACE_DEMO
    if cfg.chat_base_url:
        cfg.chat_api_key = remember(cfg.chat_base_url, cfg.chat_api_key)
    if cfg.gloss_base_url:
        cfg.gloss_api_key = remember(cfg.gloss_base_url, cfg.gloss_api_key)
    if cfg.align_base_url:
        cfg.align_api_key = remember(cfg.align_base_url, cfg.align_api_key)
    if cfg.see_base_url:
        cfg.see_api_key = remember(cfg.see_base_url, cfg.see_api_key)
    return cfg


@app.get("/api/keys")
def list_keys():
    """Slot nào server đang giữ key (chỉ trả dạng che) — UI hiện "đã lưu" thay vì ô trống."""
    return {"keys": {k: mask(v) for k, v in _keys.items()}}


@app.delete("/api/keys")
def forget_key(slot: str):
    _keys.pop(slot.rstrip("/"), None)
    try:
        KEYS_FILE.write_text(json.dumps(_keys, indent=1, ensure_ascii=False))
    except OSError:
        pass
    return {"keys": sorted(_keys)}


load_keys()


# ================================================================ Nhà cung cấp LLM
# Mỗi nhà cung cấp = một server OpenAI-compatible + model mặc định cho từng việc: model (dịch, trò chuyện, căn vệt tô),
# vision (OCR), gloss (lập bảng thuật ngữ; không có thì như model dịch), parallel (số vùng OCR gửi cùng lúc).
# Có sẵn: BUILTIN (API cloud, không sửa/xoá được). Của người dùng: data/providers.json — không chứa key (key lưu theo
# base_url, xem keys.json ở trên). Lần đầu chạy thì gieo sẵn Ollama local và các server riêng của người dùng.
# 26/09/2026: bỏ preset llama.cpp / LM Studio local; Ollama chuyển từ có sẵn sang của người dùng (sửa/xoá được).
PADDLE_OCR = "hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF:latest"   # OCR tốt nhất đã đo (18/09/2026)
BUILTIN_PROVIDERS = [
    # gloss: bench_glossary 21/09/2026 — Nemotron Ultra free lọc/dịch tốt hơn hẳn 4b
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "google/gemini-2.5-flash", "vision": "google/gemini-2.5-flash", "parallel": 4,
     "gloss": "nvidia/nemotron-3-ultra-550b-a55b:free"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini",
     "vision": "gpt-4.1-mini", "parallel": 4},
    # Lớp tương thích OpenAI của Anthropic: nhận ảnh (image_url), bỏ qua response_format và kiểu nội dung "file"
    {"id": "claude", "name": "Claude (Anthropic)", "base_url": "https://api.anthropic.com/v1", "model": "claude-opus-5",
     "vision": "claude-opus-5", "parallel": 4},
    # deepseek-flash đọc được ảnh, deepseek-v4-pro thì không (api-docs.deepseek.com, 25/09/2026)
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
     "vision": "deepseek-flash", "parallel": 4},
]
OLLAMA_PROVIDER = {"id": "ollama", "name": "Ollama (local)", "base_url": "http://localhost:11434/v1",
                   "model": "qwen3.5:latest", "vision": PADDLE_OCR, "parallel": 4}
SEED_PROVIDERS = [
    OLLAMA_PROVIDER,
    # 4b chứ không phải 9b: bench 21/09/2026 hai model ngang nhau về nghĩa mà 4b nhanh gấp 4 lần (nằm trọn trên GPU)
    {"id": "tuf", "name": "llama.cpp – server TUF (Tailscale)", "base_url": "http://100.64.0.1:8080/v1",
     "model": "4b", "vision": "paddleocr-vl", "parallel": 1},
    # Proxy OAuth tới tài khoản ChatGPT web (free) trên TUF, chỉ vào được trong LAN. gpt-6-luna căn vệt tô đúng nhất
    # (bench_align 24/09/2026: 56/60, ~3 s/lần); không nhận `temperature` (app tự bỏ); không nhận ảnh (25/09/2026).
    {"id": "chatgpt", "name": "ChatGPT (proxy OAuth trên TUF, mạng LAN)", "base_url": "http://192.168.1.10:10531/v1",
     "model": "gpt-6-luna", "vision": "", "parallel": 1},
]
PROVIDERS_FILE = DATA / "providers.json"
_PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


class Provider(BaseModel):
    id: str = ""                   # rỗng = mới (server đặt id theo tên)
    name: str
    base_url: str
    model: str = ""
    vision: str = ""
    gloss: str = ""
    parallel: int = 1


PROVIDERS_V2 = DATA / ".providers_v2"   # đã chuyển Ollama vào danh sách của người dùng (một lần; xoá rồi thì thôi)


def user_providers() -> list[dict]:
    if not PROVIDERS_FILE.exists():
        save_providers(SEED_PROVIDERS)
        PROVIDERS_V2.touch()
    try:
        items = [x for x in json.loads(PROVIDERS_FILE.read_text()) if isinstance(x, dict) and x.get("id")]
    except (OSError, ValueError):
        return []
    if not PROVIDERS_V2.exists():
        if not any(x["id"] == "ollama" for x in items):
            items = [OLLAMA_PROVIDER, *items]
            save_providers(items)
        PROVIDERS_V2.touch()
    return items


def save_providers(items: list[dict]) -> None:
    tmp = PROVIDERS_FILE.with_name(PROVIDERS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    os.replace(tmp, PROVIDERS_FILE)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode())
    return s.strip("-")[:32] or "server"


@app.get("/api/providers")
def list_providers():
    return {"providers": [{**p, "builtin": True} for p in BUILTIN_PROVIDERS]
            + [{**p, "builtin": False} for p in user_providers()]}


@app.post("/api/providers")
def save_provider(p: Provider):
    """Thêm (id rỗng) hoặc sửa một nhà cung cấp của người dùng. Không đè được nhà cung cấp có sẵn."""
    if not p.name.strip() or not p.base_url.strip():
        raise HTTPException(400, "Cần tên và Base URL")
    builtin = {x["id"] for x in BUILTIN_PROVIDERS} | {"custom"}
    items = user_providers()
    pid = p.id
    if not pid:
        base, k, taken = _slug(p.name), 1, builtin | {x["id"] for x in items}
        pid = base
        while pid in taken:
            k += 1
            pid = f"{base}-{k}"
    if pid in builtin or not _PROVIDER_ID.fullmatch(pid):
        raise HTTPException(400, "Không sửa được nhà cung cấp có sẵn")
    row = {**p.model_dump(), "id": pid, "name": p.name.strip(), "base_url": p.base_url.strip().rstrip("/"),
           "parallel": max(1, min(p.parallel, 16))}
    if any(x["id"] == pid for x in items):
        items = [row if x["id"] == pid else x for x in items]
    else:
        items.append(row)
    save_providers(items)
    return {**row, "builtin": False}


@app.delete("/api/providers/{pid}")
def delete_provider(pid: str):
    items = user_providers()
    if not any(x["id"] == pid for x in items):
        raise HTTPException(404, "Không có nhà cung cấp này (hoặc là nhà cung cấp có sẵn)")
    save_providers([x for x in items if x["id"] != pid])
    return {"deleted": pid}


# ================================================================ PDF text extraction
def get_doc(doc_id: str) -> dict:
    """Tài liệu đang mở; chưa có trong bộ nhớ (vừa khởi động lại app) thì nạp lại từ đĩa."""
    d = DOCS.get(doc_id)
    if d is None:
        if (d := store.load(doc_id)) is None:
            raise HTTPException(404, "Không tìm thấy tài liệu")
        d["lock"] = asyncio.Lock()
        apply_gloss(d)                  # bảng thuật ngữ đưa vào prompt được dựng từ bảng đã lưu, không lưu riêng
        d = DOCS.setdefault(doc_id, d)  # hai request cùng nạp một lúc: giữ bản vào trước
    return d


def should_translate(text: str) -> bool:
    t = text.strip()
    if len(t) < 3:
        return False
    if sum(c.isalpha() for c in t) / len(t) < 0.5:  # công thức, bảng số liệu
        return False
    if re.fullmatch(r"(arXiv:\S+.*|https?://\S+|\S+@\S+)", t):
        return False
    return True


# Chữ Hán/Kana/Hangul và dấu câu toàn khổ: rộng gấp đôi chữ Latin, viết liền không cách
_CJK = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿가-힯豈-﫿！-｠]")


def real_hyphen(a: str, b: str) -> bool:
    """Gạch nối cuối dòng giữa `a` và `b` là gạch của từ ghép (LLaMA3-8B, Prompt-Guard) chứ không phải gạch ngắt
    từ (trans-lation): mặc định chỉ khi một bên không phải chữ thường; xem thêm hyphen_rule."""
    return bool(a and b) and not (a[-1].islower() and b[0].islower())


def join_lines(texts: list[str], keep_hyphen=real_hyphen, styles: list[list] | None = None):
    """Nối các dòng thành đoạn: bỏ gạch ngắt từ cuối dòng (giữ gạch của từ ghép), không chèn dấu cách giữa hai chữ CJK.
    Có `styles` (kiểu từng ký tự của từng dòng) thì trả về (chữ, kiểu từng ký tự của chữ)."""
    out, st = "", []
    for k, t in enumerate(texts):
        s = styles[k] if styles else [None] * len(t)
        lead = len(t) - len(t.lstrip())
        t = t.strip()
        s = s[lead:lead + len(t)]
        if not t:
            continue
        if out.endswith(("-", "\u00ad", "\u2010")):
            a, b = re.search(r"(\w*).$", out)[1], re.match(r"\w*", t)[0]
            if out[-1] != "\u00ad" and keep_hyphen(a, b):
                out = out[:-1] + "-"
            else:
                out, st = out[:-1], st[:-1]
        elif out and not (_CJK.match(out[-1]) and _CJK.match(t[0])):
            out += " "
            st.append(None)
        out += t
        st += s
    chars, kept = [], []  # gộp khoảng trắng liên tiếp thành một dấu cách
    for ch, s in zip(out, st):
        if ch.isspace():
            if chars and chars[-1] == " ":
                continue
            ch, s = " ", None
        chars.append(ch)
        kept.append(s)
    return ("".join(chars), kept) if styles is not None else "".join(chars)


_hyphen_vocab: dict = {}


def hyphen_rule(doc: pymupdf.Document):
    """Luật giữ gạch nối cuối dòng theo cả tài liệu: giữ khi ở chỗ khác bài viết "a-b" (giữa dòng) nhiều hơn viết liền
    "ab" (English-to, heuristic-based, pre-trained), bỏ khi ngược lại (down-stream nếu bài viết downstream, tên riêng
    Prompt-Guard nếu bài viết PromptGuard); bài không có chỗ nào khác để so thì theo real_hyphen."""
    key = (doc.name, doc.page_count) if doc.name else id(doc)
    if key not in _hyphen_vocab:
        compounds, words = collections.Counter(), collections.Counter()
        for p in doc:
            t = p.get_text()
            compounds.update(f"{a}-{b}".lower() for a, b in re.findall(r"(?=\b([A-Za-z]+)-([A-Za-z]+)\b)", t))
            words.update(w.lower() for w in re.findall(r"[A-Za-z]+", t))
        if len(_hyphen_vocab) >= 16:
            _hyphen_vocab.clear()
        _hyphen_vocab[key] = compounds, words
    compounds, words = _hyphen_vocab[key]

    def keep(a: str, b: str) -> bool:
        hyphened, joined = compounds[f"{a}-{b}".lower()], words[(a + b).lower()]
        return hyphened > joined if hyphened != joined else real_hyphen(a, b)
    return keep


def text_lines(page: pymupdf.Page, textpage=None) -> list[dict]:
    """Các dòng chữ ngang của lớp chữ PDF (từng ký tự kèm font, cỡ, vị trí) và số khối PyMuPDF chứa dòng đó."""
    out = []
    # Không dùng TEXT_DEHYPHENATE: nó xoá cả gạch của từ ghép (LLaMA3-8B → LLaMA38B), join_lines tự xử lý.
    # TEXT_MEDIABOX_CLIP: bỏ chữ bị cắt khỏi vùng hiển thị (nhãn ẩn trong hình chồng lên tiêu đề → chữ vụn).
    # Không giữ chữ ghép "fi" (tách thành f + i để dịch)
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_MEDIABOX_CLIP, textpage=textpage)["blocks"]
    for bi, b in enumerate(raw):
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            if abs(ln["dir"][1]) > 0.1:  # bỏ qua chữ xoay (vd. nhãn arXiv bên lề)
                continue
            if any(not c["c"].isspace() for s in ln["spans"] for c in s["chars"]):
                out.append({"bbox": tuple(ln["bbox"]), "spans": ln["spans"], "block": bi})
    return out


def rows_of(lines: list[dict]) -> list[list[dict]]:
    """Xếp dòng theo hàng: dòng chữ thường theo đường chân chữ (khung dòng có công thức cao bất thường, tâm khung
    không tin được); mảnh bị PyMuPDF tách riêng (chỉ số của Σ, tử/mẫu phân số, dấu chấm sau công thức)
    gắn vào hàng có tâm gần nhất."""
    def main_span(ln):  # span chữ thường nhiều ký tự nhất của dòng
        spans = [s for s in ln["spans"] if not MATH_FONT.search(s["font"])]
        return max(spans, key=lambda s: sum(not c["c"].isspace() for c in s["chars"]), default=None)

    info = [(ln, main_span(ln)) for ln in lines]
    counts = [sum(not c["c"].isspace() for c in s["chars"]) if s else 0 for _, s in info]
    sizes = sorted(s["size"] for (_, s), n in zip(info, counts) if n >= 3)
    size = sizes[len(sizes) // 2] if sizes else 0
    rows: list[list] = []  # [đường chân chữ, cỡ chữ, dòng]
    frags = []
    for (ln, s), n in sorted(zip(info, counts), key=lambda x: x[0][1]["origin"][1] if x[0][1] else 0):
        if not s or n < 3 or s["size"] < 0.8 * size:
            frags.append(ln)
        elif rows and abs(s["origin"][1] - rows[-1][0]) < 0.5 * s["size"]:
            rows[-1][2].append(ln)
        else:
            rows.append([s["origin"][1], s["size"], [ln]])
    for ln in sorted(frags, key=lambda l: l["bbox"][1] + l["bbox"][3]):
        x0, y0, x1, y1 = ln["bbox"]
        # Mảnh có ký tự cỡ thường (không tính glyph căn/Σ lớn) nằm cách mọi hàng > 0,8 cỡ chữ là một dòng riêng,
        # vd. dòng cuối đoạn chỉ có "PE_pos." — gắn vào hàng trên thì chữ hai dòng bị trộn theo x
        full = collections.Counter(round(s["origin"][1], 1) for s in ln["spans"] if s["size"] >= 0.8 * size
                                   and not BIG_OP.search(s["font"]) for c in s["chars"] if not c["c"].isspace())
        if rows and rows[0][1] and full:
            base = full.most_common(1)[0][0]
            if min(abs(base - r[0]) / r[1] for r in rows) > 0.8:
                rows.append([base, size, [ln]])
                continue
        if rows and rows[0][1]:  # có hàng chữ thường: gắn vào hàng có tâm (≈ chân chữ − 0,35 cỡ) gần nhất
            min(rows, key=lambda r: abs((y0 + y1) / 2 - (r[0] - 0.35 * r[1])))[2].append(ln)
        elif rows and abs((y0 + y1) / 2 - rows[-1][0]) < 0.5 * (y1 - y0):  # toàn mảnh ngắn: xếp theo tâm khung
            rows[-1][2].append(ln)
        else:
            rows.append([(y0 + y1) / 2, 0, [ln]])
    return [r[2] for r in sorted(rows, key=lambda r: r[0])]


# Font công thức: TeX Computer Modern/AMS, txfonts/newtx/pxfonts, Latin Modern Math, STIX, Cambria Math, Symbol…
# (CMR/CMBX/CMTI là font chữ thường của bài LaTeX dùng Computer Modern nên không tính)
MATH_FONT = re.compile(r"CM(MI|SY|EX|BSY|MIB)|MSAM|MSBM|EU[FRS]M|RSFS|STMARY|WASY|(N?TX|PX)(MI|SY|EX)|MATH|SYMBOL"
                       r"|MT ?EXTRA|EUCLID", re.I)
BIG_OP = re.compile(r"CMEX|(N?TX|PX)EX", re.I)  # căn, Σ, ngoặc lớn: toạ độ chân chữ của glyph không nằm trên dòng
PLACEHOLDER = re.compile(r"\{\s*[vV]\s*(\d+)\s*\}")  # {v1}; chấp nhận biến thể LLM hay viết như { v1 }, {V1}
# Chữ đậm/nghiêng giữa đoạn (tiêu đề đầu đoạn "PromptGuard:", tên tác giả, tên hội nghị trong tài liệu tham khảo)
TAG = re.compile(r"</?[bi]>")
TAG_LOOSE = re.compile(r"<\s*(/?)\s*([bBiI])\s*>")  # "< /b >", "<B>" do LLM/Google viết lệch


def untag(text: str) -> str:
    return TAG.sub("", text)


def style_tags(text: str, styles: list, base: tuple) -> str:
    """Bọc đoạn chữ đậm/nghiêng khác kiểu nền của khối bằng <b>/<i>. `styles[k]` = (đậm, nghiêng) của ký tự k,
    None = ký tự trung tính (dấu cách, {vN}): theo kiểu hai bên nếu hai bên giống nhau."""
    eff = [None if s is None else (s[0] and not base[0], s[1] and not base[1]) for s in styles]
    plain, last = (False, False), (False, False)
    for k, e in enumerate(eff):
        if e is None:
            after = next((x for x in eff[k + 1:] if x is not None), plain)
            eff[k] = last if last == after else plain
        else:
            last = e
    out, cur = [], plain
    for ch, e in zip(text, eff):
        if e != cur:  # đóng <i> trước </b>, mở <b> trước <i> → thẻ luôn lồng đúng
            if cur[1] and (not e[1] or e[0] != cur[0]):
                out.append("</i>")
            if cur[0] and not e[0]:
                out.append("</b>")
            if e[0] and not cur[0]:
                out.append("<b>")
            if e[1] and not (cur[1] and e[0] == cur[0]):
                out.append("<i>")
            cur = e
        out.append(ch)
    return "".join(out) + ("</i>" if cur[1] else "") + ("</b>" if cur[0] else "")


def keep_tags(src: str, text: str) -> str:
    """Bản dịch phải giữ đủ và lồng đúng các thẻ <b>/<i> của bản gốc; sai thì bỏ hết thẻ (in chữ thường)."""
    text = TAG_LOOSE.sub(lambda m: f"<{m[1]}{m[2].lower()}>", text)
    stack = []
    for t in TAG.findall(text):
        if t[1] != "/":
            stack.append(t[1])
        elif not stack or stack.pop() != t[2]:
            return untag(text)
    if stack or collections.Counter(TAG.findall(text)) != collections.Counter(TAG.findall(src)):
        return untag(text)
    return text


def span_style(s: dict) -> tuple[bool, bool]:
    """(đậm, nghiêng) của span."""
    return bool(s["flags"] & 16) or "Bold" in s["font"], bool(s["flags"] & 2) or "Italic" in s["font"]


def char_key(c: dict) -> tuple:
    """Định danh một ký tự trên trang: toạ độ chân chữ (origin), trùng với toạ độ bộ lọc nội dung MuPDF thấy."""
    return round(c["origin"][0], 1), round(c["origin"][1], 1)


def row_text(row: list[dict], formulas: list[dict]) -> tuple[str, list[tuple], list]:
    """Chữ của một hàng, mỗi cụm công thức thay bằng {vN} (vùng gốc thêm vào `formulas` để vẽ lại);
    trả kèm các ký tự không phải công thức (ký tự, span) để lấy cỡ/kiểu chữ của khối, và kiểu (đậm, nghiêng)
    của từng ký tự trong chữ (None: dấu cách, {vN})."""
    # Trộn các dòng cùng hàng theo x nhưng giữ thứ tự ký tự trong từng dòng (chữ ghép "fi" tách ra có toạ độ lệch,
    # sắp lại theo x thì "five" thành "fvie")
    chars = list(heapq.merge(*[[(c, s) for s in ln["spans"] for c in s["chars"]] for ln in row],
                             key=lambda cs: cs[0]["origin"][0]))
    # Cỡ chữ và đường chân chữ chính của hàng: span chữ thường cỡ lớn (≥ 0,8 cỡ lớn nhất) nhiều ký tự nhất
    # (chỉ số dưới "model" của d_model dài hơn chữ "and" cạnh nó nhưng không phải chữ chính)
    weight = collections.Counter()
    for c, s in chars:
        if not c["c"].isspace():
            weight[round(s["size"], 1), round(s["origin"][1], 1), bool(MATH_FONT.search(s["font"]))] += 1
    top = max(k[0] for k in weight if not k[2]) if any(not k[2] for k in weight) else 0
    size, base, _ = max(weight, key=lambda k: (not k[2], k[0] >= 0.8 * top, weight[k]))

    def is_math(c, s) -> bool:
        return bool(MATH_FONT.search(s["font"])) or c["c"] == "�" or unicodedata.category(c["c"]) in ("Co", "Cn") \
            or (s["size"] < 0.8 * size and abs(s["origin"][1] - base) > 0.12 * size)  # chỉ số trên/dưới

    flags = [None if c["c"].isspace() else is_math(c, s) for c, s in chars]
    runs: list[list[int]] = []  # [đầu, cuối] các cụm công thức; hai cụm chỉ cách nhau bởi dấu/số (vd. " = ") thì gộp
    for i, m in enumerate(flags):
        if m:
            if runs and not any(chars[j][0]["c"].isalpha() for j in range(runs[-1][1] + 1, i)) \
                    and sum(f is False for f in flags[runs[-1][1] + 1:i]) <= 6:
                runs[-1][1] = i
            else:
                runs.append([i, i])
    units, plain, i = [], [], 0  # (chữ, x0, x1, kiểu): ký tự thường, hoặc cả cụm công thức thành {vN}
    for a, z in runs + [[len(chars), len(chars)]]:
        units += [(c["c"], c["bbox"][0], c["bbox"][2], None if f is None else span_style(s))
                  for (c, s), f in zip(chars[i:a], flags[i:a])]
        plain += [(c, s) for (c, s), f in zip(chars[i:a], flags[i:a]) if f is False]
        if a < len(chars):
            own = [chars[j][0] for j in range(a, z + 1) if flags[j] is not None]
            x0, x1 = min(c["bbox"][0] for c in own), max(c["bbox"][2] for c in own)
            formulas.append({"clip": (x0 - 0.3, min(c["bbox"][1] for c in own), x1 + 0.3, max(c["bbox"][3] for c in own)),
                             "base": base, "size": size, "chars": [char_key(c) for c in own]})
            units.append(("{v%d}" % len(formulas), x0, x1, None))
        i = z + 1
    # Dấu cách: PDF có ký tự cách thì giữ; không có mà hai bên cách nhau > 0,2 cỡ chữ thì thêm (hai dòng PyMuPDF
    # cùng hàng "3.2.1" | "Scaled…"; TeX chừa khoảng bằng vị trí đặt chữ trước công thức: "factor of 1/√d")
    out, styles, prev = "", [], None
    for t, x0, x1, st in units:
        if prev and not prev[0].isspace() and not t.isspace() and x0 - prev[2] > 0.2 * size:
            out += " "
            styles.append(None)
        out, prev = out + t, (t, x0, x1)
        styles += [st] * len(t)
    return out, plain, styles


def text_block(lines: list[dict], label: str | None = None, keep_hyphen=real_hyphen) -> dict | None:
    """Gộp các dòng thành một khối: khung bao, chữ (công thức thành {vN}, chữ đậm/nghiêng giữa đoạn bọc <b>/<i>),
    cỡ chữ trung vị, loại font/màu theo số đông ký tự chữ thường."""
    formulas: list[dict] = []
    texts, plain, styles, bases = [], [], [], []
    for row in rows_of(lines):
        t, p, st = row_text(row, formulas)
        texts.append(t)
        plain += p
        styles.append(st)
        if p:
            bases.append(collections.Counter(round(c["origin"][1], 1) for c, _ in p).most_common(1)[0][0])
    text, styles = join_lines(texts, keep_hyphen, styles)
    if not plain or not should_translate(PLACEHOLDER.sub("", text)):
        return None
    # Nhiều công thức hơn từ: nhãn toán trong sơ đồ bị layout nhận nhầm là đoạn chữ (rowmax, diag, V…) → không dịch
    words = len(re.findall(r"[^\W\d_]{2,}", _CJK.sub(" ", PLACEHOLDER.sub(" ", text)))) + len(_CJK.findall(text)) // 2
    if len(formulas) > words:
        return None
    count = collections.Counter(font_kind(s["font"], s["flags"]) for _, s in plain)  # email đơn cách không lấn tên
    kind = max(count, key=count.get)
    color = collections.Counter(s["color"] for _, s in plain if font_kind(s["font"], s["flags"]) == kind).most_common(1)[0][0]
    # Kiểu nền của khối: đậm/nghiêng khi ≥ 90% ký tự như vậy (tiêu đề, chú thích bảng in đậm cả câu); còn lại chữ
    # thường và phần đậm/nghiêng bọc thẻ — đếm theo ký tự, không theo span: chữ căn đều bị PDF cắt thành span
    # 1–12 ký tự, còn tiêu đề đậm đầu đoạn ("SSFTD-G: …") là một span dài
    styled = [st for st in styles if st]
    base = tuple(sum(st[k] for st in styled) >= 0.9 * len(styled) for k in (0, 1))
    sizes = sorted(s["size"] for _, s in plain)
    size = sizes[len(sizes) // 2]
    # Khoảng cách dòng gốc (khoảng cách chân chữ / cỡ chữ) và độ rộng chữ gốc (em/chữ cái) để bản dịch in giống
    # bản gốc: cùng giãn dòng, font tiếng Việt có độ rộng gần font gốc nhất (xem font_css)
    pitch = sorted(b - a for a, b in zip(bases, bases[1:]) if 0.8 * size < b - a < 2.5 * size)
    letters = [(c, s) for c, s in plain if c["c"].isalpha()]
    return {
        "bbox": (min(l["bbox"][0] for l in lines), min(l["bbox"][1] for l in lines),
                 max(l["bbox"][2] for l in lines), max(l["bbox"][3] for l in lines)),
        "text": style_tags(text, styles, base),
        "size": size,
        "leading": round(pitch[len(pitch) // 2] / size, 3) if pitch else None,
        "baselines": (bases[0], bases[-1]) if bases else None,  # chân chữ dòng đầu, dòng cuối
        "em": round(sum((c["bbox"][2] - c["bbox"][0]) / s["size"] for c, s in letters) / len(letters), 3) if letters else None,
        "color": color,
        "bold": base[0],
        "italic": base[1],
        "kind": kind,
        "formulas": formulas,
        **({"label": label} if label else {}),
    }


def by_block(lines: list[dict]) -> list[list[dict]]:
    groups: dict[int, list] = {}
    for ln in lines:
        groups.setdefault(ln["block"], []).append(ln)
    return list(groups.values())


def region_paragraphs(lines: list[dict]) -> list[list[dict]]:
    """Dòng trong một vùng layout → đoạn. Khối PyMuPDF có khoảng x giao nhau thì gộp (vd. mảnh câu bị tách
    sau ký hiệu Σ); khối nằm cạnh nhau là cột riêng (vd. hai tác giả bị PP-DocLayout gộp chung một vùng)."""
    cols: list[list] = []  # [x0, x1, dòng]
    for ls in by_block(lines):
        col = [min(l["bbox"][0] for l in ls), max(l["bbox"][2] for l in ls), ls]
        while hit := [c for c in cols if col[0] < c[1] and c[0] < col[1]]:
            for c in hit:
                cols.remove(c)
                col = [min(col[0], c[0]), max(col[1], c[1]), c[2] + col[2]]
        cols.append(col)
    return [ls for _, _, ls in cols]


def split_at_columns(lines: list[dict]) -> list[dict]:
    """PyMuPDF có khi gộp hai ô cạnh nhau thành một dòng ("Opt-based adaptive attack in evaluation" + chữ cột bên).
    Tách dòng ở khoảng trống (có khi chỉ bằng một dấu cách) mà chữ phía sau bắt đầu đúng mép trái của ≥ 2 dòng
    khác trong bảng (mép cột)."""
    starts = [l["bbox"][0] for l in lines]
    out = []
    for ln in lines:
        chars = [(c, s) for s in ln["spans"] for c in s["chars"]]
        cuts, prev = [0], None
        for k, (c, s) in enumerate(chars):
            if c["c"].isspace():
                continue
            if prev is not None and c["bbox"][0] - prev["bbox"][2] > 0.2 * s["size"] \
                    and sum(abs(x - c["bbox"][0]) < 1 for x in starts) >= 2:
                cuts.append(k)
            prev = c
        if len(cuts) == 1:
            out.append(ln)
            continue
        for a, z in zip(cuts, cuts[1:] + [len(chars)]):
            spans = []
            for c, s in chars[a:z]:
                if not spans or spans[-1][0] is not s:
                    spans.append((s, []))
                spans[-1][1].append(c)
            spans = [{**s, "chars": cs, "origin": cs[0]["origin"]} for s, cs in spans]
            own = [c for c, _ in chars[a:z] if not c["c"].isspace()]
            if own:
                out.append({"bbox": (min(c["bbox"][0] for c in own), min(c["bbox"][1] for c in own),
                                     max(c["bbox"][2] for c in own), max(c["bbox"][3] for c in own)),
                            "spans": spans, "block": ln["block"], "col_start": a > 0})
    return out


def table_cells(lines: list[dict]) -> list[list[dict]]:
    """Dòng trong vùng bảng → ô. PyMuPDF vốn tách mỗi ô thành một dòng (khối PyMuPDF thì có khi gồm cả bảng):
    gộp các mảnh sát nhau trên cùng hàng (chỉ số trên/dưới bị tách riêng), rồi nối dòng dưới vào ô phía trên khi
    rõ là câu viết tiếp (ô nhiều chữ như ví dụ prompt): dòng trên hết bằng gạch nối, hoặc dòng trên dài và dòng
    dưới bắt đầu bằng chữ thường — trừ khi hàng dưới có nhiều ô ở cột khác hơn hàng trên (dòng dưới mở đầu một hàng
    mới của bảng, vd. nhãn hàng viết thường "gender" dưới dòng tiêu đề). Các hàng của một cột cách nhau như dòng chữ
    thường nên không nối theo khoảng cách."""
    def overlap_y(a, b) -> bool:
        return min(a[3], b[3]) - max(a[1], b[1]) > 0.5 * min(a[3] - a[1], b[3] - b[1])

    cells: list[list] = []  # [khung, dòng]
    for ln in sorted(split_at_columns(lines), key=lambda l: l["bbox"][0]):
        x0, y0, x1, y1 = ln["bbox"]
        gap = 0.5 * max(s["size"] for s in ln["spans"])
        for c in cells:
            # mảnh tách ở mép cột không gộp lại với ô bên trái dù chỉ cách một dấu cách
            if overlap_y(ln["bbox"], c[0]) and x0 - c[0][2] < gap and c[0][0] - x1 < gap and not ln.get("col_start"):
                c[0] = (min(x0, c[0][0]), min(y0, c[0][1]), max(x1, c[0][2]), max(y1, c[0][3]))
                c[1].append(ln)
                break
        else:
            cells.append([ln["bbox"], [ln]])

    def text(ls) -> str:
        return " ".join("".join(c["c"] for s in l["spans"] for c in s["chars"]).strip()
                        for l in sorted(ls, key=lambda l: l["bbox"][0]))

    def mates(row, x0, x1) -> int:  # số ô cùng hàng nằm ngoài khoảng x của ô đang xét (ô của cột khác)
        return sum(overlap_y(row, b) and (b[2] <= x0 + 1 or b[0] >= x1 - 1) for b, _ in cells)

    out: list[dict] = []
    for box, ls in sorted(cells, key=lambda c: (c[0][1], c[0][0])):
        t, size = text(ls), max(s["size"] for l in ls for s in l["spans"])
        for c in out:
            cx0, cy0, cx1, cy1 = c["box"]
            row = c["row"]
            if c["rows"] > 1 and overlap_y(box, row) and box[0] >= cx0 - 1 and box[2] <= cx1 + 1:
                # từ bị căn giãn của dòng cuối ô nhiều dòng ("negative  rugby  Marcel  movie")
                c["lines"] += ls
                c["row"] = (min(row[0], box[0]), row[1], max(row[2], box[2]), max(row[3], box[3]))
                c["text"] = f"{c['text']} {t}"
                break
            if -0.3 * size < box[1] - row[3] < 0.6 * size \
                    and (abs(row[0] - box[0]) < size or min(row[2], box[2]) - max(row[0], box[0])
                         > 0.5 * min(row[2] - row[0], box[2] - box[0])) \
                    and (c["text"].endswith("-") or t[:1].islower() and len(c["text"]) >= 25) \
                    and mates(box, min(cx0, box[0]), max(cx1, box[2])) <= mates(row, cx0, cx1):
                c["lines"] += ls
                c["box"] = (min(cx0, box[0]), cy0, max(cx1, box[2]), box[3])
                c["row"], c["text"], c["rows"] = box, t, c["rows"] + 1
                break
        else:
            out.append({"box": box, "lines": ls, "row": box, "text": t, "rows": 1})
    return [c["lines"] for c in out]


_WRAP_MARK = "↪↩"  # ↪ (hoặc ↩ ghép với →): dòng code bị ngắt, phần sau viết tiếp dòng trên (minted)


def line_chars(ln: dict) -> str:
    return "".join(c["c"] for s in ln["spans"] for c in s["chars"])


def prose_words(lines: list[dict], kind: str) -> tuple[int, int]:
    """(số từ văn xuôi, số token code) của một đoạn trong vùng thuật toán/code; `kind` = loại font chính của vùng.
    Code: chữ khác font chính (Query đơn cách, User không chân giữa thuật toán chữ có chân), nghiêng (tên biến args,
    res), có "_" (tool_calls, 'get_channels_0'). Văn xuôi: từ thường ≥ 2 chữ cái cùng font chính. Còn lại không
    tính bên nào: từ đậm (từ khoá let/in/function), dính ký hiệu toán (Q𝑖, max(𝑚(), toàn hoa (LOOP, FRESH), một chữ
    cái, khoá "content:", gọi hàm "rowmax(S(", tên có số (FlashAttention-2) — thuật toán kiểu FlashAttention-2 dày
    công thức mà vẫn là câu văn; riêng khối code (font chính đơn cách) thì từ dính ký hiệu là code."""
    prose = code = 0
    for ln in lines:
        tokens, cur = [], []
        for s in ln["spans"]:
            for c in s["chars"]:
                if c["c"].isspace():
                    tokens, cur = tokens + [cur] if cur else tokens, []
                else:
                    cur.append((c["c"], s))
        tokens += [cur] if cur else []
        for tok in tokens:
            word = "".join(ch for ch, _ in tok)
            letters = [(ch, s) for ch, s in tok if ch.isalpha()]
            if len(letters) < 2 or all(ch.isupper() for ch, _ in letters) or word.rstrip(",").endswith(":") \
                    or any(MATH_FONT.search(s["font"]) for _, s in tok):
                continue
            s = letters[0][1]
            bold, italic = span_style(s)
            if bold:
                continue
            core = word.lstrip("(\"“‘'`").rstrip(".,;)\"”’'`?!")
            if italic or font_kind(s["font"], s["flags"]) != kind or "_" in core:
                code += 1
            elif re.fullmatch(r"[^\W\d_]+(['’-][^\W\d_]+)*", core):
                prose += 1
            elif kind == "mono":  # khối code: send_message(to=user, #read_emails_0) — "_" vẽ bằng nét, không phải ký tự
                code += 1
    return prose, code


def algorithm_lines(lines: list[dict]) -> list[list[dict]]:
    """Dòng trong vùng thuật toán/code → từng dòng riêng (gộp thành một đoạn thì bản dịch chảy thành văn xuôi, mất
    thụt lề và số dòng). Hàng có khoảng trống rộng (> 1,5 cỡ chữ: chú thích "// Update memory" căn phải) hay số dòng
    là một dòng PyMuPDF riêng thì tách thành nhiều mảnh (số dòng giữ nguyên, bản dịch bắt đầu đúng chỗ thụt lề); dòng mở đầu bằng ↪, hay hàng không đánh số giữa các dòng đánh số, nối
    vào mảnh phía trên. Chỉ giữ mảnh có văn xuôi
    (tiêu đề "Algorithm 2 …", "Parameters: Tool set …", chú thích): dòng toàn từ khoá/tên biến/JSON dịch ra là hỏng
    code ("action" → "hành động"), để nguyên bản gốc."""
    letters = collections.Counter(font_kind(s["font"], s["flags"]) for l in lines for s in l["spans"]
                                  if not MATH_FONT.search(s["font"]) for c in s["chars"] if c["c"].isalpha())
    kind = letters.most_common(1)[0][0] if letters else "serif"
    rows = [sorted(row, key=lambda l: l["bbox"][0]) for row in rows_of(lines)]
    numbered = [bool(re.match(r"\s*\d{1,3}[:.](\s|$)", " ".join(line_chars(l) for l in row))) for row in rows]
    seen = False
    segs: list[list[dict]] = []
    for row, num in zip(rows, numbered):
        # thuật toán đánh số dòng: hàng không số sau dòng 1 là phần viết tiếp của bước trên (câu dài bị xuống dòng)
        wrap = seen and not num and sum(numbered) >= 3
        seen |= num
        sizes = sorted(s["size"] for l in row for s in l["spans"])
        size = sizes[len(sizes) // 2]
        parts = [[row[0]]]
        for ln in row[1:]:
            lone_num = len(parts) == 1 and num and re.fullmatch(r"\s*\d{1,3}[:.]\s*", line_chars(parts[0][0]))
            if lone_num or ln["bbox"][0] - max(l["bbox"][2] for l in parts[-1]) > 1.5 * size:
                parts.append([ln])
            else:
                parts[-1].append(ln)
        for p in parts:
            first = "".join(line_chars(l) for l in p).strip()[:1]
            x0, x1 = p[0]["bbox"][0], max(l["bbox"][2] for l in p)
            above = [s for s in segs if min(max(l["bbox"][2] for l in s), x1) > max(min(l["bbox"][0] for l in s), x0)]
            if wrap and above:
                above[-1].extend(p)
            elif first and first in _WRAP_MARK and above:
                # bỏ dòng chỉ có dấu ↪: nằm trong khung đoạn nên vẫn bị xoá, khỏi thành {vN} lơ lửng giữa bản dịch
                above[-1].extend(l for l in p if set(line_chars(l)) - set(_WRAP_MARK + "→ "))
            else:
                segs.append(list(p))
    out = []
    for s in segs:
        prose, code = prose_words(s, kind)
        if prose >= 2 and prose >= 0.75 * (prose + code):
            out.append(s)
    return out


def extract_blocks(page: pymupdf.Page, textpage=None) -> list[dict]:
    """Lấy các đoạn văn bản cùng vị trí, cỡ chữ, màu, đậm/nghiêng.
    Có PP-DocLayout: gom dòng theo vùng (một đoạn = một vùng, bỏ công thức/hình/số trang; bảng dịch từng ô).
    Dòng không thuộc vùng nào, hoặc không nạp được model layout → gom theo khối của PyMuPDF như cũ."""
    lines = text_lines(page, textpage)
    regions = detect_layout(page) if lines else None
    groups, rest = [], lines
    if regions:
        per_region: list[list] = [[] for _ in regions]
        rest = []
        for ln in lines:
            best, i = max((_inter(ln["bbox"], g["bbox"]), i) for i, g in enumerate(regions))
            if best < 0.5 * _area(ln["bbox"]):
                rest.append(ln)
            elif regions[i]["label"] == "table" or regions[i]["label"] not in LAYOUT_SKIP:
                per_region[i].append(ln)
        groups = [(p, g["label"]) for g, ls in zip(regions, per_region) if ls
                  for p in (table_cells(ls) if g["label"] == "table" else
                            algorithm_lines(ls) if g["label"] == "algorithm" else region_paragraphs(ls))]
    groups += [(ls, None) for ls in by_block(rest)]
    keep_hyphen = hyphen_rule(page.parent)
    return [b for b in (text_block(ls, label, keep_hyphen) for ls, label in groups) if b]


def page_has_text(page: pymupdf.Page) -> bool:
    return len(page.get_text("text").strip()) >= 50


# Ký tự "rác" khi font thiếu bảng mã ToUnicode: ký tự thay thế và vùng dùng riêng
_BAD_CHARS = re.compile(r"[\ufffd\ue000-\uf8ff]")


def text_is_garbled(page: pymupdf.Page) -> bool:
    """Lớp chữ đọc ra rác (PDF cũ, font không có bảng mã) → nên OCR lại thay vì dịch rác.
    Ngưỡng 25%: trang dày công thức LaTeX cũng có ký tự vùng dùng riêng (đo được tối đa 4% trên 10 bài bench),
    còn trang hỏng thật thì rác gần như toàn bộ."""
    t = re.sub(r"\s", "", page.get_text("text"))
    return len(t) >= 50 and len(_BAD_CHARS.findall(t)) > 0.25 * len(t)


# ================================================================ Layout (PP-DocLayoutV3, ONNX)
LAYOUT_PATH = BASE / "models" / "layout" / "PP-DocLayoutV3.onnx"
# Bản ONNX cộng đồng (Paddle2ONNX) — ghim đúng commit + kiểm tra SHA-256 để repo có đổi cũng không ảnh hưởng
LAYOUT_URL = ("https://huggingface.co/alex-dinh/PP-DocLayoutV3-ONNX/resolve/"
              "7952bce3e684c7aa90aa7bf47798e8efae3c0921/PP-DocLayoutV3.onnx")
LAYOUT_SHA256 = "d24809294b2f9f1a9a2767043a64df2714b66e5be056887be2233d1117d784f6"
LAYOUT_LABELS = ["abstract", "algorithm", "aside_text", "chart", "content", "display_formula", "doc_title",
                 "figure_title", "footer", "footer_image", "footnote", "formula_number", "header", "header_image",
                 "image", "inline_formula", "number", "paragraph_title", "reference", "reference_content", "seal",
                 "table", "text", "vertical_text", "vision_footnote"]
# Vùng không dịch nguyên khối: hình, công thức, số trang, con dấu, chữ dọc bên lề (vd. dấu arXiv);
# bảng thì dịch từng ô (xem table_cells, lines_to_blocks)
LAYOUT_SKIP = {"aside_text", "chart", "display_formula", "inline_formula", "formula_number", "image", "header_image",
               "footer_image", "number", "seal", "table", "vertical_text"}
_layout: dict = {}
_layout_lock = threading.Lock()
# Huỷ phiên onnxruntime (layout, RapidOCR) trước khi Python dọn dẹp: để đến lúc huỷ biến toàn cục thì onnxruntime
# trên macOS có khi báo "recursive_mutex lock failed" khi thoát
atexit.register(lambda: (_layout.pop("sess", None), _rapid.clear()))
LAYOUT_RETRY = 600  # tải/nạp lỗi → trong 10 phút không thử lại (khỏi chờ mạng ở mỗi trang)


def layout_session():
    """Nạp PP-DocLayoutV3 (lần đầu tự tải ~130MB). Có khoá: nhiều trang dịch song song chỉ tải một lần."""
    with _layout_lock:
        if "sess" in _layout:
            return _layout["sess"]
        if time.monotonic() - _layout.get("failed_at", -LAYOUT_RETRY) < LAYOUT_RETRY:
            raise RuntimeError(_layout["error"])
        try:
            import onnxruntime as ort
            if not LAYOUT_PATH.exists():
                LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp, sha = LAYOUT_PATH.with_suffix(".part"), hashlib.sha256()
                with httpx.stream("GET", LAYOUT_URL, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            sha.update(chunk)
                if sha.hexdigest() != LAYOUT_SHA256:
                    tmp.unlink()
                    raise RuntimeError("file PP-DocLayoutV3 tải về sai SHA-256")
                tmp.rename(LAYOUT_PATH)
            _layout["sess"] = ort.InferenceSession(str(LAYOUT_PATH), providers=["CPUExecutionProvider"])
            return _layout["sess"]
        except Exception as e:
            _layout.update(failed_at=time.monotonic(), error=repr(e))
            raise


def _inter(a, b) -> float:
    """Diện tích giao nhau của hai hình chữ nhật (x0, y0, x1, y1)."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(a) -> float:
    return max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)


def detect_layout(page: pymupdf.Page, threshold: float = 0.5) -> list[dict] | None:
    """Các vùng bố cục (toạ độ trang, theo thứ tự đọc). None nếu không nạp được model."""
    import numpy as np
    try:
        sess = layout_session()
    except Exception as e:
        if _layout.get("warned") != _layout.get("failed_at"):  # mỗi lần lỗi chỉ báo một lần
            print(f"[layout] không nạp được PP-DocLayoutV3 ({e}) → gộp dòng theo khoảng cách, thử lại sau 10 phút")
            _layout["warned"] = _layout.get("failed_at")
        return None
    r = page.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(800 / r.width, 800 / r.height), alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    img = np.pad(img, ((0, max(0, 800 - pix.height)), (0, max(0, 800 - pix.width)), (0, 0)), mode="edge")[:800, :800]
    x = np.ascontiguousarray((img.astype(np.float32) / 255).transpose(2, 0, 1)[None])  # KHÔNG chuẩn hoá kiểu ImageNet
    out = sess.run(None, {"im_shape": np.array([[800, 800]], np.float32), "image": x,
                          "scale_factor": np.array([[800 / r.height, 800 / r.width]], np.float32)})[0]
    regions = [{"label": LAYOUT_LABELS[int(o[0])], "bbox": tuple(float(v) for v in o[2:6]), "order": float(o[6])}
               for o in out if o[1] >= threshold]
    if any(g["label"] == "reference_content" for g in regions):
        # "reference" là khung bao cả danh sách tài liệu tham khảo; giữ nó thì bước lọc dưới bỏ từng mục
        # và cả trang thành một khối → bỏ khung, mỗi mục là một đoạn
        regions = [g for g in regions if g["label"] != "reference"]
    kept = []  # bỏ vùng nằm gọn trong vùng lớn hơn (vd. "Abstract—" trong khối abstract)
    for g in sorted(regions, key=lambda g: -_area(g["bbox"])):
        if not any(_inter(g["bbox"], k["bbox"]) > 0.8 * _area(g["bbox"]) for k in kept):
            kept.append(g)
    return sorted(kept, key=lambda g: g["order"])


# ================================================================ OCR
_rapid: dict = {}


def ocr_block(bbox, text: str, heights: list[float], **extra) -> dict | None:
    text = re.sub(r"\s+", " ", text).strip()
    if not should_translate(text):
        return None
    hs = sorted(heights) or [12.5]
    return {"bbox": tuple(bbox), "text": text, "size": hs[len(hs) // 2] * 0.8, "color": 0,
            "bold": False, "italic": False, "kind": "serif", "ocr": True, **extra}


def lines_to_blocks(page: pymupdf.Page, lines: list[tuple], regions: list[dict] | None = None) -> list[dict]:
    """Gom dòng OCR (x0, y0, x1, y1, text) theo vùng PP-DocLayout: đúng đoạn, đúng thứ tự đọc,
    bỏ qua hình/bảng/công thức. Dòng không thuộc vùng nào (hoặc không có model layout) → merge_lines."""
    if regions is None:
        regions = detect_layout(page)
    if not regions:
        return merge_lines(lines)
    groups: list[list] = [[] for _ in regions]
    rest = []
    for ln in lines:
        best, i = max((_inter(ln, g["bbox"]), i) for i, g in enumerate(regions))
        (groups[i] if best >= 0.5 * _area(ln) else rest).append(ln)
    blocks = []
    for g, ls in zip(regions, groups):
        if g["label"] == "table":  # bảng: dịch từng ô chữ như trước (số liệu bị should_translate bỏ qua)
            rest += ls
        if not ls or g["label"] in LAYOUT_SKIP:
            continue
        ls.sort(key=lambda l: (round(l[1] / 4), l[0]))
        bbox = (min(l[0] for l in ls), min(l[1] for l in ls), max(l[2] for l in ls), max(l[3] for l in ls))
        b = ocr_block(bbox, join_lines([l[4] for l in ls]), [l[3] - l[1] for l in ls],
                      label=g["label"], region=g["bbox"])
        if b:
            blocks.append(b)
    return blocks + merge_lines(rest)


def merge_lines(lines: list[tuple]) -> list[dict]:
    """Gộp các dòng OCR (x0, y0, x1, y1, text) thành đoạn văn theo vị trí."""
    lines = sorted(lines, key=lambda l: (round(l[1]), l[0]))
    paras: list[dict] = []
    for x0, y0, x1, y1, text in lines:
        h = y1 - y0
        target = None
        for p in reversed(paras[-6:]):
            lx0, ly0, lx1, ly1 = p["last"]
            lh = ly1 - ly0
            overlap = min(x1, p["x1"]) - max(x0, p["x0"])
            if (0 <= y0 - ly1 < 0.8 * lh or -0.3 * lh < y0 - ly1 < 0) and overlap > 0.3 * min(x1 - x0, p["x1"] - p["x0"]) \
                    and 0.7 < h / lh < 1.4:
                target = p
                break
        if target:
            target["text"] = join_lines([target["text"], text])
            target["x0"], target["x1"] = min(target["x0"], x0), max(target["x1"], x1)
            target["y1"] = max(target["y1"], y1)
            target["last"] = (x0, y0, x1, y1)
            target["heights"].append(h)
        else:
            paras.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text,
                          "last": (x0, y0, x1, y1), "heights": [h]})
    blocks = []
    for p in paras:
        b = ocr_block((p["x0"], p["y0"], p["x1"], p["y1"]), p["text"], p["heights"])
        if b:
            blocks.append(b)
    return blocks


def rapid_lines(page: pymupdf.Page, lang: str, zoom: float = 2.0) -> list[tuple]:
    """RapidOCR (PP-OCR chạy ONNX) → các dòng (x0, y0, x1, y1, text). Lần đầu tự tải model (~15MB)."""
    from rapidocr import LangRec, RapidOCR
    code = {"eng": "EN", "en": "EN", "chi": "CH", "ch": "CH", "vie": "LATIN", "vi": "LATIN"}.get(lang.lower(), lang.upper())
    if _rapid.get(code) is None:
        _rapid[code] = RapidOCR(params={"Rec.lang_type": getattr(LangRec, code, LangRec.EN)})
    # 144dpi: đo trên 3 trang, 150dpi lại đọc sai nhiều dòng (93% so với 99%); zoom lớn hơn dễ bỏ sót dòng
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    res = _rapid[code](pix.tobytes("png"))
    lines = []
    for box, text, score in zip(res.boxes if res.boxes is not None else [], res.txts or [], res.scores or []):
        if float(score) < 0.5:
            continue
        xs, ys = [pt[0] for pt in box], [pt[1] for pt in box]
        lines.append((min(xs) / zoom, min(ys) / zoom, max(xs) / zoom, max(ys) / zoom, text))
    return lines


def ocr_rapid(page: pymupdf.Page, lang: str) -> list[dict]:
    return lines_to_blocks(page, rapid_lines(page, lang))


def ocr_tesseract(page: pymupdf.Page, lang: str) -> list[dict]:
    try:
        tp = page.get_textpage_ocr(dpi=300, full=True, language=lang)
    except Exception as e:
        raise HTTPException(500, f"Tesseract lỗi (đã cài `brew install tesseract tesseract-lang`?): {e}")
    lines = []
    for b in page.get_text("dict", textpage=tp)["blocks"]:
        for ln in b.get("lines", []):
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if text:
                lines.append((*ln["bbox"], text))
    return lines_to_blocks(page, lines)


async def ocr_space(page: pymupdf.Page, cfg: Config) -> list[dict]:
    zoom = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = pix.tobytes("jpg", jpg_quality=80)
    lang = {"eng": "eng", "vie": "vnm"}.get(cfg.ocr_lang, cfg.ocr_lang)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post("https://api.ocr.space/parse/image",
                              data={"apikey": cfg.ocr_api_key or "helloworld", "language": lang,
                                    "isOverlayRequired": "true", "OCREngine": "2", "scale": "true"},
                              files={"file": ("page.jpg", img, "image/jpeg")})
    data = r.json()
    if data.get("IsErroredOnProcessing"):
        raise HTTPException(502, f"OCR.space lỗi: {data.get('ErrorMessage')}")
    lines = []
    for res in data.get("ParsedResults", []):
        for ln in (res.get("TextOverlay") or {}).get("Lines", []):
            ws = ln["Words"]
            x0 = min(w["Left"] for w in ws); y0 = min(w["Top"] for w in ws)
            x1 = max(w["Left"] + w["Width"] for w in ws); y1 = max(w["Top"] + w["Height"] for w in ws)
            lines.append((x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom, ln["LineText"]))
    return await asyncio.to_thread(lines_to_blocks, page, lines)


VLM_OCR_PROMPT = ("Transcribe ALL text in this image exactly as written, in reading order. "
                  "Join wrapped lines into one paragraph with spaces, undo end-of-line hyphenation, "
                  "write math inline in LaTeX. Output only the transcription, no comments.")


def strip_repeats(text: str) -> str:
    """Cắt khi cả đoạn bắt đầu lặp lại từ đầu — lỗi lặp thường gặp của model OCR nhỏ.
    Chỉ 30 ký tự đầu trùng thì chưa đủ (câu sau có thể lặp lại cụm mở đầu), phải trùng tới ≥80 ký tự hoặc hết đoạn."""
    head = text[:30]
    k = text.find(head, 30) if len(text) > 60 else -1
    while k > 0:
        n = min(80, k, len(text) - k)
        if text[k:k + n] == text[:n]:
            return text[:k].strip()
        k = text.find(head, k + 1)
    return text


def dedupe_sentences(text: str) -> str:
    """Bỏ câu (≥20 ký tự) lặp lại y hệt trong cùng đoạn — model OCR hay chép lại một câu hai lần."""
    seen, out = set(), []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        key = re.sub(r"\W+", "", sent).lower()
        if len(key) >= 20 and key in seen:
            continue
        seen.add(key)
        out.append(sent)
    return " ".join(out)


_LAYOUT_TAG = re.compile(r"^\s*[a-z_]+\s*\[\s*\d+(?:\s*,\s*\d+){3}\s*\]\s*")


def clean_vlm_lines(text: str) -> str:
    """Bỏ tiền tố `nhãn [x0, y0, x1, y1]` (Unlimited-OCR/DeepSeek-OCR) và các dòng lặp lại."""
    seen, out = set(), []
    for line in text.splitlines():
        line = _LAYOUT_TAG.sub("", line).strip()
        key = re.sub(r"\W+", "", line).lower()
        if not key or key in seen or any(key in s for s in seen if len(key) >= 20):
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def vlm_ocr_prompt(model: str) -> str:
    m = model.lower()
    if "glm-ocr" in m:  # prompt chính thức của GLM-OCR
        return "Text Recognition:"
    if "paddleocr-vl" in m:  # prompt chính thức của PaddleOCR-VL cho vùng chữ
        return "OCR:"
    if "deepseek-ocr" in m:
        return "Free OCR."
    return VLM_OCR_PROMPT


def is_fullpage_ocr(model: str) -> bool:
    """Model tự phân tích bố cục cả trang và trả bbox → gửi cả trang một lần thay vì từng vùng."""
    return "unlimited-ocr" in model.lower()


_UOCR_LINE = re.compile(r"^\s*([a-z_]+)\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*(.*)$")


def uocr_skip(label: str) -> bool:
    """Nhãn Unlimited-OCR không dịch: hình, bảng, công thức (chú thích/tiêu đề hình vẫn dịch)."""
    return bool(re.search(r"image|table|formula|equation|chart|seal|aside|vertical", label)) and \
        not re.search(r"caption|title|footnote", label)


def latex_to_text(s: str) -> str:
    """`\\( ^{*} \\)` → `*`, `\\dagger` → `†`… (chỉ số trên/dưới Unlimited-OCR xuất dạng LaTeX)."""
    s = re.sub(r"\\\(\s*[\^_]\{\s*(.*?)\s*\}\s*\\\)", r"\1", s)
    s = re.sub(r"\\(ddagger|dagger|ast)\b", lambda m: {"ddagger": "‡", "dagger": "†", "ast": "*"}[m[1]], s)
    return re.sub(r"\s+([,.;:])", r"\1", s)


def fullpage_blocks(page: pymupdf.Page, content: str, lang: str, truncated: bool = False) -> list[dict]:
    """Ghép kết quả cả trang của Unlimited-OCR với PP-DocLayout làm "trọng tài":
    bỏ dòng không có bbox / không khớp vùng layout nào (thường là chữ bịa),
    vùng layout bị model bỏ sót → đọc bù bằng RapidOCR."""
    W, H = page.rect.width, page.rect.height
    items: list[list] = []
    for line in content.splitlines():
        m = _UOCR_LINE.match(line)
        if m:
            items.append([m[1], (int(m[2]) * W / 1000, int(m[3]) * H / 1000, int(m[4]) * W / 1000, int(m[5]) * H / 1000), m[6]])
        elif items and line.strip():  # dòng nối tiếp của vùng trước; dòng trước nhãn đầu tiên bị bỏ
            items[-1][2] += "\n" + line.strip()
    if truncated and items:  # chạm max_tokens/ngữ cảnh: khối cuối bị cắt dở → bỏ, để RapidOCR đọc bù
        items.pop()
    regions = detect_layout(page)
    tables = [g["bbox"] for g in regions or [] if g["label"] == "table"]
    lines = rapid_lines(page, lang)  # để kiểm tra chéo, ước lượng cỡ chữ và đọc bù vùng bị sót

    def words(s: str) -> collections.Counter:
        return collections.Counter(re.findall(r"\w+", s.lower()))

    cands = []  # (điểm khớp với RapidOCR, thứ tự, khối)
    for i, (label, rect, text) in enumerate(items):
        if uocr_skip(label) or (regions and not any(_inter(rect, g["bbox"]) > 0.3 * _area(rect) for g in regions)):
            continue
        if any(_inter(rect, t) > 0.5 * _area(rect) for t in tables):  # bảng do RapidOCR dịch từng ô → tránh dịch hai lần
            continue
        text = dedupe_sentences(strip_repeats(latex_to_text(re.sub(r"-\n", "", clean_vlm_lines(text)))))
        inside = sorted((l for l in lines if _inter(l, rect) >= 0.5 * _area(l)), key=lambda l: (round(l[1] / 4), l[0]))
        b = ocr_block(rect, text, [l[3] - l[1] for l in inside], label=label)
        if not b:
            continue
        ref, got = words(" ".join(l[4] for l in inside)), words(b["text"])
        score = sum((got & ref).values()) / max(sum(got.values()), 1) if ref else 1.0
        if sum(ref.values()) >= 20 and sum(got.values()) > 1.25 * sum(ref.values()):
            b["text"], score = re.sub(r"\s+", " ", join_lines([l[4] for l in inside])).strip(), 1.0  # model lặp câu → dùng RapidOCR
        cands.append((score, i, b))
    # Khối chồng nhau (model lặp lại / đặt nhầm chỗ): giữ khối khớp RapidOCR nhất; khớp < 50% → coi là bịa
    kept: list[tuple] = []
    for score, i, b in sorted(cands, key=lambda c: -c[0]):
        if score >= 0.5 and not any(_inter(b["bbox"], k[2]["bbox"]) > 0.5 * min(_area(b["bbox"]), _area(k[2]["bbox"]))
                                    for k in kept):
            kept.append((score, i, b))
    blocks = [b for _, _, b in sorted(kept, key=lambda k: k[1])]
    covered = [b["bbox"] for b in blocks] + [rect for label, rect, _ in items if uocr_skip(label)]
    for g in regions or []:
        if g["label"] != "table" and (g["label"] in LAYOUT_SKIP or
                                      sum(_inter(g["bbox"], c) for c in covered) >= 0.3 * _area(g["bbox"])):
            continue
        blocks += lines_to_blocks(page, [l for l in lines if _inter(l, g["bbox"]) >= 0.5 * _area(l)], [g])
    return blocks


async def ocr_vlm_fullpage(page: pymupdf.Page, cfg: Config, client: httpx.AsyncClient, url: str, headers: dict) -> list[dict]:
    png = page.get_pixmap(dpi=150).tobytes("png")
    body = {"model": cfg.ocr_vlm_model, "temperature": 0, "max_tokens": 4000,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                {"type": "text", "text": "<image>\ndocument parsing."},  # prompt duy nhất Unlimited-OCR trả kết quả
            ]}]}
    try:
        r = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Không kết nối được vision LLM tại {url}: {e!r}")
    if r.status_code >= 400:
        raise HTTPException(502, f"Lỗi vision LLM {r.status_code}: {r.text[:300]}")
    choice = r.json()["choices"][0]
    truncated = choice.get("finish_reason") == "length"
    if truncated:
        print(f"[ocr] trang {page.number + 1}: Unlimited-OCR chạm giới hạn token, phần thiếu đọc bù bằng RapidOCR")
    return await asyncio.to_thread(fullpage_blocks, page, choice["message"]["content"] or "", cfg.ocr_lang, truncated)


async def ocr_vlm(page: pymupdf.Page, cfg: Config) -> list[dict]:
    """PP-DocLayout + RapidOCR chia vùng, vision LLM đọc lại chữ từng vùng (chính xác hơn với scan xấu).
    Riêng Unlimited-OCR: gửi cả trang một lần."""
    headers = {"Content-Type": "application/json"}
    if cfg.ocr_vlm_api_key:
        headers["Authorization"] = f"Bearer {cfg.ocr_vlm_api_key}"
    url = cfg.ocr_vlm_base_url.rstrip("/") + "/chat/completions"
    if is_fullpage_ocr(cfg.ocr_vlm_model):
        async with httpx.AsyncClient(timeout=600) as client:  # lần đầu gọi còn phải nạp model
            return await ocr_vlm_fullpage(page, cfg, client, url, headers)
    blocks = await asyncio.to_thread(ocr_rapid, page, cfg.ocr_lang)
    # Chỉ đọc lại vùng layout và đoạn đủ dài; ô bảng/mẩu chữ ngắn giữ RapidOCR (model OCR hay bịa với ảnh quá nhỏ)
    todo = [b for b in blocks if "region" in b or len(b["text"]) >= 40]
    sem = asyncio.Semaphore(max(1, cfg.ocr_vlm_concurrency))

    async def read(client: httpx.AsyncClient, b: dict):
        rect = pymupdf.Rect(b["bbox"])
        if "region" in b:
            rect |= pymupdf.Rect(b["region"])
        rect += (-3, -3, 3, 3)
        # ≥150dpi, phóng to vùng chữ nhỏ để mỗi dòng cao ≥ ~40px
        zoom = min(4.0, max(150 / 72, 40 / max(b["size"] / 0.8, 1)))
        png = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
        body = {"model": cfg.ocr_vlm_model, "temperature": 0,
                "max_tokens": len(b["text"]) * 2 + 1024,  # chặn sinh lặp vô hạn, chừa chỗ cho model vẫn "suy nghĩ"
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": vlm_ocr_prompt(cfg.ocr_vlm_model)},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                ]}]}
        if re.search(r"ocr", cfg.ocr_vlm_model, re.I):
            # Model OCR chuyên dụng không "suy nghĩ" và hay lặp tới hết max_tokens (GLM-OCR) → chặn sát độ dài đoạn.
            # Đo 20/09/2026 trên TUF: mức cũ (len//2 + 64) cắt mất 1/38 vùng ở scan 60 dpi vì chữ đọc ra dài hơn
            # chữ gốc, nên nới lên 3/4 độ dài mà vẫn chặn được kiểu lặp vô hạn
            body["max_tokens"] = len(b["text"]) * 3 // 4 + 96
            body["stop"] = ["```"]
        no_think(cfg, url, body)
        async with sem:
            try:
                r = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                raise HTTPException(502, f"Không kết nối được vision LLM tại {url}: {e!r}")
        if r.status_code >= 400:
            raise HTTPException(502, f"Lỗi vision LLM {r.status_code}: {r.text[:300]}")
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":  # bị cắt hoặc lặp tới hết max_tokens → giữ chữ RapidOCR
            print(f"[ocr] trang {page.number + 1}: vision LLM chạm max_tokens ở đoạn {b['text'][:40]!r}, giữ RapidOCR")
            return
        text = re.sub(r"<think>.*?</think>", "", choice["message"]["content"] or "", flags=re.S)
        text = clean_vlm_lines(re.sub(r"```[a-z]*", "", text))
        text = dedupe_sentences(strip_repeats(latex_to_text(re.sub(r"\s+", " ", text).strip())))
        if text:  # VLM trả rỗng thì giữ kết quả RapidOCR
            b["text"] = text

    async with httpx.AsyncClient(timeout=600) as client:  # lần đầu gọi còn phải nạp model
        await asyncio.gather(*(read(client, b) for b in todo))
    return blocks


async def ocr_custom(page: pymupdf.Page, cfg: Config) -> list[dict]:
    """Gửi ảnh trang tới endpoint tuỳ chỉnh.

    Request : POST multipart  file=<page.png>, lang=<ocr_lang>   (Authorization: Bearer <key> nếu có)
    Response: {"lines": [{"bbox": [x0, y0, x1, y1], "text": "..."}]}  — toạ độ theo pixel của ảnh gửi đi
              (cũng chấp nhận "box": [[x,y] x4] kiểu PaddleOCR)
    """
    if not cfg.ocr_custom_url:
        raise HTTPException(400, "Chưa nhập URL cho OCR tuỳ chỉnh")
    zoom = 2.0
    png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    headers = {"Authorization": f"Bearer {cfg.ocr_custom_api_key}"} if cfg.ocr_custom_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(cfg.ocr_custom_url, headers=headers, data={"lang": cfg.ocr_lang},
                                  files={"file": ("page.png", png, "image/png")})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Không kết nối được OCR tuỳ chỉnh: {e!r}")
    if r.status_code >= 400:
        raise HTTPException(502, f"OCR tuỳ chỉnh lỗi {r.status_code}: {r.text[:300]}")
    lines = []
    for ln in r.json().get("lines", []):
        if "bbox" in ln:
            x0, y0, x1, y1 = ln["bbox"]
        else:
            xs, ys = [p[0] for p in ln["box"]], [p[1] for p in ln["box"]]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if ln.get("text", "").strip():
            lines.append((x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom, ln["text"]))
    return await asyncio.to_thread(lines_to_blocks, page, lines)


def needs_ocr(page: pymupdf.Page, cfg: Config) -> bool:
    return cfg.ocr_mode == "force" or (cfg.ocr_mode == "auto"
                                       and (not page_has_text(page) or text_is_garbled(page)))


async def get_blocks(page: pymupdf.Page, cfg: Config) -> tuple[list[dict], bool]:
    if not needs_ocr(page, cfg):  # cũng chạy PP-DocLayout (~0,6s/trang, lần đầu còn tải model) → không chặn event loop
        return await asyncio.to_thread(extract_blocks, page), False
    if cfg.ocr_engine == "tesseract":
        return await asyncio.to_thread(ocr_tesseract, page, cfg.ocr_lang), True
    if cfg.ocr_engine == "ocrspace":
        return await ocr_space(page, cfg), True
    if cfg.ocr_engine == "vlm":
        return await ocr_vlm(page, cfg), True
    if cfg.ocr_engine == "custom":
        return await ocr_custom(page, cfg), True
    return await asyncio.to_thread(ocr_rapid, page, cfg.ocr_lang), True


# ================================================================ Rendering
def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def background_color(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple:
    """Màu nền phổ biến nhất trong vùng (để che chữ trên trang scan)."""
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(0.5, 0.5))
    if pix.width * pix.height == 0:
        return (1, 1, 1)
    n, s = pix.n, pix.samples
    counts = collections.Counter(tuple(s[i:i + 3]) for i in range(0, len(s) - n + 1, n * 2))
    r, g, b = counts.most_common(1)[0][0]
    return (r / 255, g / 255, b / 255)


# ---------------------------------------------------------------- Fonts
SYS = "/System/Library/Fonts/Supplemental"
FONT_DIR = BASE / "fonts"
# tên -> (loại, {kiểu: đường dẫn}); thứ tự trong mỗi loại = thứ tự ưu tiên khi "auto"
FONTS: dict[str, tuple[str, dict[str, str]]] = {
    "Source Serif 4": ("serif", {k: str(FONT_DIR / f"SourceSerif4-{v}.ttf") for k, v in
                                 {"regular": "Regular", "bold": "Bold", "italic": "It", "bolditalic": "BoldIt"}.items()}),
    "Noto Serif": ("serif", {k: str(FONT_DIR / f"NotoSerif-{v}.ttf") for k, v in
                             {"regular": "Regular", "bold": "Bold", "italic": "Italic", "bolditalic": "BoldItalic"}.items()}),
    "Times New Roman": ("serif", {k: f"{SYS}/Times New Roman{v}.ttf" for k, v in
                                  {"regular": "", "bold": " Bold", "italic": " Italic", "bolditalic": " Bold Italic"}.items()}),
    "Georgia": ("serif", {k: f"{SYS}/Georgia{v}.ttf" for k, v in
                          {"regular": "", "bold": " Bold", "italic": " Italic", "bolditalic": " Bold Italic"}.items()}),
    "Be Vietnam Pro": ("sans", {k: str(FONT_DIR / f"BeVietnamPro-{v}.ttf") for k, v in
                                {"regular": "Regular", "bold": "SemiBold", "italic": "Italic", "bolditalic": "SemiBoldItalic"}.items()}),
    "Noto Sans": ("sans", {k: str(FONT_DIR / f"NotoSans-{v}.ttf") for k, v in
                           {"regular": "Regular", "bold": "Bold", "italic": "Italic", "bolditalic": "BoldItalic"}.items()}),
    "Arial": ("sans", {k: f"{SYS}/Arial{v}.ttf" for k, v in
                       {"regular": "", "bold": " Bold", "italic": " Italic", "bolditalic": " Bold Italic"}.items()}),
    "Courier New": ("mono", {k: f"{SYS}/Courier New{v}.ttf" for k, v in
                             {"regular": "", "bold": " Bold", "italic": " Italic", "bolditalic": " Bold Italic"}.items()}),
}
GENERIC = {"serif": "serif", "sans": "sans-serif", "mono": "monospace"}


def font_available(name: str) -> bool:
    return name in FONTS and Path(FONTS[name][1]["regular"]).exists()


def font_kind(fontname: str, flags: int) -> str:
    n = fontname.lower()
    if flags & 8 or re.search(r"mono|courier|consol|menlo|cmtt|typewriter|code", n):
        return "mono"
    if re.search(r"sans|arial|helvet|calibri|verdana|inter|roboto|cmss|segoe", n):
        return "sans"
    if flags & 4 or re.search(r"times|serif|roman|cmr|cmbx|cmti|nimbus|georgia|garamond|palat|minion|stix|charter|cambria|libertin|bookman", n):
        return "serif"
    return "sans"


_font_em: dict[str, float] = {}


def font_em(name: str) -> float:
    """Độ rộng trung bình một chữ cái của font (em), đo trên chữ tiếng Anh để so được với chữ gốc trong PDF."""
    if name not in _font_em:
        text = "".join(ch for ch in "The dominant sequence transduction models are based on complex recurrent or "
                       "convolutional neural networks that include an encoder and a decoder" if ch.isalpha())
        _font_em[name] = pymupdf.Font(fontfile=FONTS[name][1]["regular"]).text_length(text, 1) / len(text)
    return _font_em[name]


def pick_font(cfg: "Config", kind: str, em: float | None = None) -> str | None:
    """Font đã chọn trong cấu hình; "auto": font có độ rộng chữ gần font gốc nhất (`em`, đo từ PDF) để bản dịch
    giữ được cỡ chữ gốc — bài LaTeX font Times (0,42 em) in bằng Source Serif 4 (0,47 em) thì chữ phải co ~6%."""
    want = {"serif": cfg.font_serif, "sans": cfg.font_sans, "mono": cfg.font_mono}[kind]
    if want != "auto" and font_available(want):
        return want
    names = [n for n, (k, _) in FONTS.items() if k == kind and font_available(n)]
    if em and names:
        return min(names, key=lambda n: abs(font_em(n) - em))
    return names[0] if names else None


def font_css(cfg: "Config", em: dict[str, float] | None = None) -> tuple[str, pymupdf.Archive, dict[str, str], dict]:
    """@font-face cho font đã chọn; trả về (css, archive, {loại: font-family}, {loại: (ascender, descender)}).
    `em`: độ rộng chữ gốc theo loại font."""
    arch, faces, families, metrics = pymupdf.Archive(), [], {}, {}
    for kind in ("serif", "sans", "mono"):
        name = pick_font(cfg, kind, (em or {}).get(kind))
        if not name:
            families[kind] = GENERIC[kind]
            continue
        fam = f"vi-{kind}"
        families[kind] = fam
        ft = pymupdf.Font(fontfile=FONTS[name][1]["regular"])
        metrics[kind] = (ft.ascender, abs(ft.descender))
        for style, path in FONTS[name][1].items():
            if not Path(path).exists():
                path = FONTS[name][1]["regular"]
            folder = str(Path(path).parent)
            arch.add(folder)
            weight = "bold" if "bold" in style else "normal"
            italic = "italic" if "italic" in style else "normal"
            faces.append(f"@font-face {{font-family: {fam}; src: url('{Path(path).name}');"
                         f" font-weight: {weight}; font-style: {italic};}}")
    return "\n".join(faces), arch, families, metrics


@app.get("/api/fonts")
def list_fonts():
    return {"fonts": [{"name": n, "kind": k, "available": font_available(n)} for n, (k, _) in FONTS.items()]}


_GAP_PNG: dict[int, bytes] = {}


def gap_png(k: int) -> bytes:
    """PNG trong suốt rộng k+1 px, cao 1 px: giữ chỗ cho công thức thứ k (bề rộng điểm ảnh cho biết là công thức nào)."""
    if k not in _GAP_PNG:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, k + 1, 1), 1)
        pix.set_alpha(bytes(k + 1))  # alpha = 0 (clear_with chỉ xoá màu, alpha vẫn 255 → ô đen)
        _GAP_PNG[k] = pix.tobytes("png")
    return _GAP_PNG[k]


def formula_html(vi: str, formulas: list[dict], lift: dict[int, float], first: int = 0) -> str:
    """Bản dịch → HTML: {vN} thành ảnh trong suốt rộng bằng công thức gốc, cao bằng phần công thức nằm trên
    chân chữ (ảnh inline của MuPDF đặt đáy trên chân chữ và tính cả ảnh là phần trên chân chữ).
    Phần dưới chân chữ (mẫu số, chỉ số dưới) vẽ tràn xuống; nếu đè dòng sau thì `lift[k]` = cỡ chữ của một ký tự
    rộng 0 đặt sau công thức k, chỉ để dòng đó có thêm khoảng dưới chân chữ.
    `first`: số thứ tự ảnh giữ chỗ đầu tiên của khối (ảnh đánh số chung cả trang, xem gap_png)."""
    used = set()

    def img(m) -> str:
        k = int(m[1]) - 1
        if not 0 <= k < len(formulas) or k in used:  # số lạ, hoặc LLM chép một công thức hai lần → bỏ
            return ""
        used.add(k)
        x0, y0, x1, _ = formulas[k]["clip"]
        pad = f'<span style="font-size:{lift[k]:.1f}px">&#8203;</span>' if lift.get(k) else ""
        return (f'<img src="v{first + k}.png" style="width:{x1 - x0:.2f}px;'
                f' height:{max(formulas[k]["base"] - y0, 0.1):.2f}px">{pad}')
    return PLACEHOLDER.sub(img, re.sub(r"&lt;(/?[bi])&gt;", r"<\1>", html_escape(vi)))


def formula_box(im_bbox, f: dict, s: float = 1.0) -> tuple:
    """Vị trí vẽ công thức từ ảnh giữ chỗ (đáy ảnh = chân chữ); s = tỉ lệ co của khối."""
    x0, _, x1, base = im_bbox
    return x0, base - (f["base"] - f["clip"][1]) * s, x1, base + (f["clip"][3] - f["base"]) * s


_warned_filter = False


def formula_source(src: pymupdf.Document, pno: int, formulas: list[dict]) -> pymupdf.Document:
    """Bản sao trang gốc chỉ giữ ký tự của công thức (hình, nét vẽ giữ nguyên). Khung font của dấu căn/phân số
    cao hơn nét chữ nên vùng cắt công thức lấn sang dòng trên/dưới: chép thẳng từ trang gốc sẽ kéo theo mẩu chữ
    khác (vd. "ma v" trên √d_k); redact theo ô cũng không được vì khung √ trùm lên chữ dòng trên, xoá chữ là mất √.
    → lọc từng ký tự theo toạ độ chân chữ bằng bộ lọc nội dung của MuPDF (API nội bộ của PyMuPDF; lỗi thì chép nguyên)."""
    global _warned_filter
    # tuple(): khối nạp lại từ state.json (khởi động lại app) có toạ độ dạng list — không cho vào set được
    own = {tuple(k) for f in formulas for k in f["chars"]}
    doc = pymupdf.open()
    doc.insert_pdf(src, from_page=pno, to_page=pno)
    try:
        class TextFilter(mupdf.PdfSanitizeFilterOptions2):  # MuPDF hỏi từng ký tự: trả 1 = bỏ
            def __init__(self):
                super().__init__()
                self.use_virtual_text_filter()
                self.use_virtual_image_filter()

            def image_filter(self, ctx, ctm, name, image, scissor):
                return None  # công thức không cần ảnh: bỏ, khỏi nhúng lại ảnh của trang vào PDF

            def text_filter(self, ctx, ucsbuf, ucslen, trm, ctm, bbox, tr, ca, CA):
                m = mupdf.fz_concat(mupdf.fz_concat(mupdf.FzMatrix(trm), mupdf.FzMatrix(ctm)), page_ctm)
                p = mupdf.fz_transform_point(mupdf.FzPoint(0, 0), m)  # chân chữ, toạ độ trang như PyMuPDF
                return 0 if any((round(p.x + dx, 1), round(p.y + dy, 1)) in own
                                for dx in (0, -0.05, 0.05) for dy in (0, -0.05, 0.05)) else 1

        page = pymupdf._as_pdf_page(doc[0])
        page_ctm = mupdf.FzMatrix()
        mupdf.pdf_page_transform(page, mupdf.FzRect(), page_ctm)
        opts = TextFilter()
        mupdf.pdf_filter_page_contents(page.doc(), page, pymupdf._make_PdfFilterOptions(
            recurse=1, instance_forms=1, ascii=1, sanitize=1, sopts=opts))
    except Exception as e:  # PyMuPDF đổi API nội bộ → vẫn chạy, chỉ có thể dính mẩu chữ dòng bên
        if not _warned_filter:
            print(f"[render] không lọc được ký tự quanh công thức ({e!r}), chép nguyên vùng từ trang gốc")
            _warned_filter = True
        doc = pymupdf.open()
        doc.insert_pdf(src, from_page=pno, to_page=pno)
    return doc


def ink_clip(page: pymupdf.Page, f: dict) -> tuple:
    """Vùng cắt công thức sát theo nét mực theo chiều dọc (chiều ngang giữ theo bề rộng ký tự để giữ khoảng cách):
    khung font cao hơn nét chữ nhiều → ảnh giữ chỗ cao quá mức, dòng bị dãn."""
    import numpy as np
    x0, y0, x1, y1 = clip = formula_reach(f)
    pix = page.get_pixmap(clip=pymupdf.Rect(clip), matrix=pymupdf.Matrix(4, 4), colorspace=pymupdf.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    rows = np.nonzero(a.min(axis=1) < np.percentile(a, 90) - 60)[0]
    if not len(rows):
        return f["clip"]
    # phần nới thêm của formula_reach: chỉ lấy mực liền với khung ký tự (đuôi "y"), dừng ở hàng trống đầu tiên —
    # bên dưới có thể là công thức dòng sau (bản sao trang giữ mọi công thức của trang)
    last, end = rows[0], (f["clip"][3] - pix.y / 4) * 4
    for r in rows[1:]:
        if r > end and r > last + 1:
            break
        last = r
    return x0, max(y0, (pix.y + rows[0]) / 4 - 0.3), x1, min(y1, (pix.y + last + 1) / 4 + 0.3)


def formula_reach(f: dict) -> tuple:
    """Vùng có thể có mực của công thức. Khung ký tự lấy theo descender của font; font khai báo descender ≈ 0
    (NewTXMI của newtx: −0,009 em) thì khung chỉ xuống quá chân chữ 0,2pt → đuôi "y", dấu phẩy bị cắt. Đáy khung
    sát chân chữ thấp nhất (< 0,1 cỡ chữ; font đúng thì ≥ 0,2) → nới xuống 0,3 cỡ chữ, ink_clip bó lại theo mực."""
    x0, y0, x1, y1 = f["clip"]
    low = max((k[1] for k in f.get("chars", ())), default=None)
    if low is not None and y1 - low < 0.1 * f["size"]:
        y1 = max(y1, low + 0.3 * f["size"])
    return x0, y0, x1, y1


def descent_overlaps(tmp: pymupdf.Page, formulas: list[dict], first: int = 0) -> dict[int, float]:
    """Công thức nào (trên trang dàn chữ tạm của một khối) có phần dưới chân chữ đè lên chữ dòng sau, đè bao nhiêu pt."""
    lines = [(ln["spans"][0]["origin"][1], max(s["size"] for s in ln["spans"]), ln["bbox"])
             for b in tmp.get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"]
             if any(s["text"].strip() for s in ln["spans"])]
    bad: dict[int, float] = {}
    for im in tmp.get_image_info():
        k = im["width"] - 1 - first
        x0, _, x1, bottom = formula_box(im["bbox"], formulas[k])
        base = im["bbox"][3]  # đáy ảnh giữ chỗ = chân dòng chứa công thức
        for oy, size, lb in lines:  # đỉnh chữ dòng sau ≈ chân chữ − 0,7 cỡ; lệch dưới 0,5pt thì bỏ qua như TeX
            if oy > base + 1 and lb[0] < x1 and lb[2] > x0 and bottom - (oy - 0.7 * size) > 0.5:
                bad[k] = max(bad.get(k, 0), bottom - (oy - 0.7 * size))
    return bad


FIT_PERCENTILE = 0.5  # cỡ chữ chung của nhóm khối cùng kiểu = phân vị này của cỡ vừa khung từng khối (xem dưới)
FIT_PERCENTILE_TABLE = 0.5  # riêng ô bảng


def free_boxes(page: pymupdf.Page, blocks: list[dict], todo: list[int]) -> dict[int, pymupdf.Rect]:
    """Khung nới của từng khối vào khoảng trống sẵn có (đọc trang TRƯỚC khi xoá chữ), để bản dịch dài hơn bản gốc
    không phải co chữ: xuống dưới tới vật cản gần nhất (chữ, ảnh, nét vẽ, viền hộp) — tối đa một dòng; sang phải
    (ô bảng, khối một dòng) tới vật cản gần nhất — tối đa rộng thêm 60%. Chừa 0,3 cỡ chữ trước vật cản."""
    obst = [pymupdf.Rect(l["bbox"]) for b in page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]
            for l in b.get("lines", [])]
    obst += [pymupdf.Rect(im["bbox"]) for im in page.get_image_info()]
    frames = [pymupdf.Rect(d["rect"]) for d in page.get_drawings()]
    out = {}
    for i in todo:
        b = blocks[i]
        r, size = pymupdf.Rect(b["bbox"]), b["size"]
        mine = [o for o in obst if r.contains((o.tl + o.br) / 2)]
        others = [o for o in obst + frames if not any(o is m for m in mine)]
        # hộp/khung bao quanh khối (hộp ví dụ prompt, ô bảng có kẻ): cạnh dưới/phải của nó là giới hạn
        around = [f for f in frames if f.x0 <= r.x0 + 1 and f.x1 >= r.x1 - 1 and f.y0 <= r.y0 + 1 and f.y1 >= r.y1 - 1]
        below = [o.y0 for o in others if o.x0 < r.x1 - 1 and o.x1 > r.x0 + 1 and o.y0 >= r.y1 - 1]
        y1 = min(below + [f.y1 for f in around] + [page.rect.y1 - 20])
        y1 = min(y1 - 0.3 * size, r.y1 + (b.get("leading") or 1.2) * size)
        x1 = r.x1
        if b.get("label") == "table" or r.height < 1.6 * size:
            right = [o.x0 for o in others if o.y0 < r.y1 - 1 and o.y1 > r.y0 + 1 and o.x0 >= r.x1 - 1]
            x1 = min(right + [f.x1 for f in around] + [page.rect.x1 - 20])
            x1 = min(x1 - 0.3 * size, r.x1 + 0.6 * r.width)
        if y1 > r.y1 + 0.5 or x1 > r.x1 + 0.5:
            out[i] = pymupdf.Rect(r.x0, r.y0, max(x1, r.x1), max(y1, r.y1))
    return out


def redact_rect(rect: pymupdf.Rect, lines: list[pymupdf.Rect]) -> pymupdf.Rect:
    """Vùng xoá chữ của khối: MuPDF xoá cả ký tự chỉ chồng mép vùng ~2pt, nên dòng của khối bên trên/dưới mà khung
    chạm vào (công thức cao của bước 12 chồng lên dòng "11: end for" trong thuật toán) bị mất theo. Lùi mép vùng
    khỏi các dòng đó; ký tự của chính khối vẫn nằm sâu trong vùng nên vẫn bị xoá."""
    r = pymupdf.Rect(rect)
    for ln in lines:
        c = (ln.tl + ln.br) / 2
        if rect.contains(c) or ln.x0 >= rect.x1 or ln.x1 <= rect.x0:
            continue
        if c.y < rect.y0 and ln.y1 > r.y0:
            r.y0 = ln.y1 - 1
        elif c.y > rect.y1 and ln.y0 < r.y1:
            r.y1 = ln.y0 + 1
    return r if r.height > 0.5 * rect.height else rect


def render_translation(page: pymupdf.Page, blocks: list[dict], translations: dict[int, str], cfg: "Config",
                       src: pymupdf.Document | None = None) -> dict[int, dict]:
    """Xoá chữ gốc trong khung và chèn bản dịch vào đúng vị trí, tự co chữ cho vừa.
    Công thức ({vN}) được chép nguyên dạng vector từ trang gốc `src` vào chỗ giữ trong bản dịch.
    Các khối cùng cỡ/kiểu chữ co cùng một tỉ lệ để cỡ chữ trên trang đều nhau."""
    fills = {}
    wider = free_boxes(page, blocks, [i for i, b in enumerate(blocks) if translations.get(i) and not b.get("ocr")])
    lines = [pymupdf.Rect(l["bbox"]) for b in page.get_text("dict")["blocks"] if b["type"] == 0 for l in b["lines"]]
    for i, b in enumerate(blocks):
        if not translations.get(i):
            continue
        rect = pymupdf.Rect(b["bbox"])
        if b.get("ocr"):  # chữ nằm trong ảnh → phủ màu nền lên
            fills[i] = background_color(page, rect + (-1, -1, 1, 1))
        else:  # không tô trắng: khối nằm trên nền màu (hộp code, hàng bảng tô xám) sẽ thành ô trắng, chữ trắng biến mất
            page.add_redact_annot(redact_rect(rect, lines), fill=False)
    # Xoá cả nét vẽ nằm gọn trong khung chữ (vạch phân số, gạch trên căn thức): công thức sẽ được vẽ lại chỗ mới
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
    # Phủ nền cho mọi khối OCR trước rồi mới viết chữ: khung các dòng OCR sát/chồng nhau,
    # phủ xen kẽ thì nền của dòng sau sẽ che mất chữ vừa viết của dòng trước
    for i, fill in fills.items():
        page.draw_rect(pymupdf.Rect(blocks[i]["bbox"]) + (-1, -1, 1, 1), color=None, fill=fill, overlay=True)
    # Độ rộng chữ gốc theo loại font (trung bình theo số ký tự) → chọn font tiếng Việt rộng tương đương
    width = collections.defaultdict(lambda: [0.0, 0])
    for i, b in enumerate(blocks):
        if translations.get(i) and b.get("em"):
            width[b.get("kind", "sans")][0] += b["em"] * len(b["text"])
            width[b.get("kind", "sans")][1] += len(b["text"])
    faces, archive, families, metrics = font_css(cfg, {k: w / n for k, (w, n) in width.items()})
    # Giãn dòng như bản gốc; khối một dòng (tiêu đề, chú thích ngắn) lấy giãn dòng phổ biến của trang
    leads = sorted(b["leading"] for i, b in enumerate(blocks) if translations.get(i) and b.get("leading"))
    page_lead = leads[len(leads) // 2] if leads else 1.2
    # Lề 1px nằm ở div (lề body chỉ áp dụng cho lần đặt đầu tiên của Story, các khối sau sẽ lệch 1px)
    css = faces + " body {margin: 0} div {margin: 0; padding: 1px; text-align: justify}"
    # Công thức (vùng cắt đã bó sát nét mực) của từng khối, chép từ bản trang gốc chỉ còn ký tự công thức
    formulas: dict[int, list[dict]] = {}
    fdoc = None
    used = [f for i, b in enumerate(blocks) if translations.get(i) for f in b.get("formulas", ())]
    if src is not None and used:
        fdoc = formula_source(src, page.number, used)
        formulas = {i: [{**f, "clip": ink_clip(fdoc[0], f)} for f in b["formulas"]]
                    for i, b in enumerate(blocks) if translations.get(i) and b.get("formulas")}
    first, n = {}, 0  # ảnh giữ chỗ công thức đánh số chung cả trang: khối i dùng ảnh first[i], first[i] + 1…
    for i in range(len(blocks)):
        if translations.get(i):
            first[i], n = n, n + len(formulas.get(i, ()))
    for g in range(n):
        archive.add(gap_png(g), f"v{g}.png")

    def html(i: int, lift: dict, brk: bool = False) -> str:
        b = blocks[i]
        color = (0x000000 if sum(fills[i]) > 1.5 else 0xFFFFFF) if i in fills else b["color"]
        style = (f"font-family: {families[b.get('kind', 'sans')]}; font-size: {b['size']:.1f}px; color: #{color:06x};"
                 f" line-height: {b.get('leading') or page_lead:.3f};"
                 f" font-weight: {'bold' if b['bold'] else 'normal'}; font-style: {'italic' if b['italic'] else 'normal'}"
                 + ("; page-break-after: always" if brk else ""))
        return f'<div style="{style}">{formula_html(translations[i], formulas.get(i, []), lift, first[i])}</div>'

    def fit(i: int, rect: pymupdf.Rect, lift: dict) -> float:
        story = pymupdf.Story(html(i, lift), user_css=css, archive=archive)
        f = story.fit_scale(pymupdf.Rect(0, 0, rect.width, rect.height), scale_min=1,
                            flags=pymupdf.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW)
        return 1 / f.parameter if f.big_enough else 0  # tỉ lệ co lớn nhất vẫn vừa khung

    def layout_rect(b: dict) -> pymupdf.Rect:
        """Khung dựng: khung chữ gốc chỉ cao từ đỉnh chữ dòng đầu tới đáy chữ dòng cuối, còn mỗi dòng dựng cần cả
        khoảng giãn dòng + lề 1px — ô bảng một dòng chữ 6pt cao 6pt, dựng cần ~9pt nên chữ phải co còn 65%. Tính lại
        theo chân chữ gốc để dòng đầu/cuối của bản dịch nằm đúng chân chữ gốc và vừa đúng số dòng gốc ở cỡ gốc."""
        r = pymupdf.Rect(b["bbox"])
        if b.get("ocr") or not b.get("baselines"):
            return r
        lead = b.get("leading") or page_lead
        a, d = metrics.get(b.get("kind", "sans"), (0.9, 0.22))
        half = max(lead - a - d, 0) / 2 * b["size"]  # nửa khoảng giãn dòng trên/dưới (mô hình dòng của CSS)
        top, bottom = b["baselines"]
        return pymupdf.Rect(r.x0, top - a * b["size"] - half - 1, r.x1, max(bottom + d * b["size"] + half + 1, r.y1))

    placed = {}  # i → (khung, tỉ lệ co)
    for i, b in enumerate(blocks):
        if not translations.get(i):
            continue
        rect = layout_rect(b)
        rect.x1 += 1  # nới nhẹ để tránh cắt chữ cuối
        s = fit(i, rect, {})
        if s < 0.98 and i in wider:  # dài hơn khung → dùng khoảng trống bên dưới / bên phải
            big = pymupdf.Rect(rect.x0, rect.y0, max(rect.x1, wider[i].x1), max(rect.y1, wider[i].y1))
            if (s2 := fit(i, big, {})) > 1.02 * s:
                rect, s = big, s2
        if s:
            placed[i] = (rect, s)

    # Cỡ chữ đều: khối cùng kiểu chữ in cùng một cỡ = trung vị (FIT_PERCENTILE) của (cỡ gốc × tỉ lệ co vừa khung)
    # trong nhóm: một đoạn dịch quá dài không kéo cả trang nhỏ theo; đoạn nào cần nhỏ hơn nữa thì vẫn co riêng.
    # Đo 19/09/2026 trên bản dịch qwen3.5 thật: phân vị 25% + Source Serif + giãn dòng 1,15 → chữ còn 87% cỡ gốc;
    # trung vị + font rộng như gốc + giãn dòng gốc + nới khung vào khoảng trống → 99,8%, các đoạn chênh ±0,04pt.
    # Cùng kiểu = cùng cỡ gốc (làm tròn 1pt)/loại font/đậm/nghiêng; trang scan không có font thật, cỡ chữ chỉ là
    # ước lượng từ chiều cao dòng (lệch ±0,6pt) → nhóm theo nhãn layout (thân bài, chú thích hình, tiêu đề…)
    # Ô bảng khung sát chữ, bản dịch dài hơn là phải co nhiều → nhóm riêng, không kéo chữ thân bài nhỏ theo
    def style(b):
        return ("ocr", b.get("label")) if b.get("ocr") else \
            (round(b["size"]), b.get("kind"), b["bold"], b["italic"], b.get("label") == "table")
    groups = collections.defaultdict(list)
    for i, (_, s) in placed.items():
        groups[style(blocks[i])].append(blocks[i]["size"] * s)
    final = {}  # i → (khung, tỉ lệ co, lift)
    for i, (rect, s_fit) in placed.items():
        size = blocks[i]["size"]
        g = sorted(groups[style(blocks[i])])
        pct = FIT_PERCENTILE_TABLE if blocks[i].get("label") == "table" else FIT_PERCENTILE
        target = g[min(int(len(g) * pct), len(g) - 1)] / size
        s, lift = min(s_fit, target), {}
        for _ in range(3):  # công thức đè dòng sau → thêm khoảng dưới chân chữ cho dòng đó, dàn lại
            if i not in formulas:
                break
            box = pymupdf.Rect(0, 0, rect.width / s, rect.height / s)
            tmp = pymupdf.Story(html(i, lift), user_css=css, archive=archive).write_with_links(lambda *_: (box, box, None))
            bad = descent_overlaps(tmp[0], formulas[i], first[i])
            if not bad:
                break
            new = dict(lift)
            for k, o in bad.items():
                new[k] = max(new.get(k, 0), size) + 5 * o  # phần dưới chân chữ ≈ 0,2 × cỡ chữ
            if not (s_new := fit(i, rect, new)):
                break
            lift, s = new, min(s_new, target)
        final[i] = (rect, s, lift)
    if not final:
        return {}

    # Dàn mọi khối của trang trong MỘT Story (mỗi khối một div, ngắt trang giữa các div) lên một trang tạm rồi dán
    # một lần: mỗi Story tự nhúng một bản font, dàn từng khối riêng như insert_htmlbox thì trang 15 khối nhúng
    # 15 bản font (bài 15 trang → 350 bản, PDF 2 MB thành 20 MB)
    order = list(final)
    story = pymupdf.Story("".join(html(i, final[i][2], brk=j < len(order) - 1) for j, i in enumerate(order)),
                          user_css=css, archive=archive)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    dev = writer.begin_page(page.rect)
    for i in order:
        rect, s, _ = final[i]
        h = rect.height / s
        # Khung đặt cao dư: nội dung đã được fit() cho vừa h nên cách xuống dòng không đổi, nhưng nếu lấp VỪA KHÍT h
        # thì MuPDF xử lý lệch lệnh ngắt trang sau div → nội dung các khối sau trượt sang khung kế tiếp (ô "FNR"
        # mất chữ, chữ ô bên cạnh nhảy vào)
        story.place(pymupdf.Rect(0, 0, rect.width / s, h * 1.5 + 20))
        story.draw(dev, pymupdf.Matrix(s, 0, 0, s, rect.x0, rect.y0))  # co đúng tỉ lệ s, đặt vào khung của khối
    writer.end_page()
    writer.close()
    tmp = pymupdf.open("pdf", buf.getvalue())
    page.show_pdf_page(page.rect, tmp, 0)
    owner = {first[i] + k: (i, k) for i in order for k in range(len(formulas.get(i, ())))}
    for im in tmp[0].get_image_info() if fdoc else ():
        i, k = owner[im["width"] - 1]
        f = formulas[i][k]
        page.show_pdf_page(pymupdf.Rect(formula_box(im["bbox"], f, final[i][1])), fdoc, 0, clip=f["clip"])
    return {i: {"scale": s, "rect": tuple(r)} for i, (r, s, _) in final.items()}  # để bench đo cỡ chữ, vị trí


# ================================================================ Translators
class LLMError(Exception):
    pass


class PromptCut(LLMError):
    """Server nhận được ít token hơn hẳn số đã gửi → prompt bị cắt (Ollama cắt âm thầm); chia nhỏ lô rồi gửi lại."""


_ITEM = re.compile(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_json(content: str) -> dict:
    """JSON của model. Hỏng (model nói thêm, hoặc bị cắt vì chạm trần token) thì vớt lại các mục còn nguyên vẹn,
    phần thiếu sẽ được dịch lại riêng — hơn là bỏ cả lô."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    items = [{"id": int(i), "text": json.loads(f'"{t}"')} for i, t in _ITEM.findall(content)]
    if not items:
        raise ValueError("LLM không trả về JSON")
    print(f"[llm] JSON hỏng/bị cắt → vớt được {len(items)} đoạn")
    return {"items": items}


# ---------------------------------------------------------------- Token & ngữ cảnh model
# Đo trên qwen3.5 (19/09/2026): tiếng Anh 4,7 ký tự/token, tiếng Việt 3,5, chữ Hán 1,7 → lấy thấp hơn một chút cho
# tokenizer khác. Prompt vượt ngữ cảnh thì Ollama CẮT ÂM THẦM (gửi ~5.500 token, model chỉ nhận 2.050) → chia lô
# theo token, không theo ký tự: 2.500 chữ Hán ≈ 4.000–5.000 token cả vào lẫn ra, trang bảng 155 ô ≈ 4.000 token
# chỉ riêng khung JSON.
def est_tokens(text: str) -> int:
    t = untag(text)
    cjk = len(_CJK.findall(t))
    return int((len(t) - cjk) / 4.0 + cjk / 1.5) + 1


def in_tokens(item: dict) -> int:
    """Token đoạn chiếm ở đầu vào: chữ gốc + ngữ cảnh prev/next + khung JSON (id, max_len, ngoặc)."""
    return est_tokens(item["text"]) + est_tokens(item.get("prev", "") + item.get("next", "")) + 20


def out_tokens(item: dict) -> int:
    """Token bản dịch tiếng Việt dự kiến (Anh→Việt dài hơn ~20%; một chữ Hán ≈ một âm tiết Việt ~4 ký tự)."""
    t = untag(item["text"])
    cjk = len(_CJK.findall(t))
    return int(((len(t) - cjk) * 1.2 + cjk * 4) / 3.2) + 10


def item_tokens(item: dict) -> int:
    return in_tokens(item) + out_tokens(item)


CTX_STEPS = (4096, 8192, 16384, 32768)


def doc_num_ctx(doc: pymupdf.Document, cfg: "Config") -> int:
    """Ngữ cảnh Ollama cho cả tài liệu (cố định: đổi num_ctx là Ollama nạp lại model ~8 giây): đủ cho trang nặng nhất
    trong một lượt gọi. qwen3.5 thêm ngữ cảnh gần như không tốn RAM (4096 → 16384: 5,06 → 5,38 GB)."""
    if cfg.num_ctx:
        return cfg.num_ctx
    need = 0
    for page in doc:
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        need = max(need, sum(item_tokens({"text": b[4]}) for b in blocks))
    fixed = est_tokens(SYSTEM_PROMPT + cfg.glossary) + 200  # prompt hệ thống, ngữ cảnh câu, dư
    return next((c for c in CTX_STEPS if 0.85 * c >= need + fixed), CTX_STEPS[-1])


_servers: dict[tuple, tuple[str, int, int]] = {}
CLOUD_CTX = 32768   # API ngoài không cho biết ngữ cảnh → mức an toàn cho hầu hết model hiện nay
CLOUD_SLOTS = 4     # API ngoài chịu được nhiều request song song; server local thì theo số slot thật


async def server_info(base_url: str, model: str = "") -> tuple[str, int, int]:
    """(loại server, ngữ cảnh mỗi lượt gọi, số lượt gọi chạy song song được). Hỏi một lần cho mỗi (URL, model):
    - "ollama": app tự đặt num_ctx qua /api/chat, ngữ cảnh trả về là mức tối đa của model (/api/show);
    - "llamacpp": n_ctx và --parallel của slot; chế độ router (tự nạp model) trả n_ctx = 0 ở /props nên lấy
      --ctx-size, --parallel của đúng preset trong /v1/models;
    - "other" (OpenRouter, OpenAI, LM Studio…): lấy context_length của model trong /v1/models nếu server có trả,
      không thì CLOUD_CTX."""
    key = (base_url, model)
    if key not in _servers:
        root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
        info = ("other", CLOUD_CTX, CLOUD_SLOTS)
        async with httpx.AsyncClient(timeout=8) as client:
            async def models() -> list[dict]:
                r = await client.get(f"{base_url.rstrip('/')}/models", headers=(
                    {"Authorization": f"Bearer {cfg_key}"} if (cfg_key := _keys.get(base_url.rstrip("/"))) else {}))
                return r.json().get("data", []) if r.status_code == 200 else []
            try:
                if (await client.get(f"{root}/api/version")).status_code == 200:
                    r = await client.post(f"{root}/api/show", json={"model": model})
                    mi = r.json().get("model_info", {}) if r.status_code == 200 else {}
                    top = max((v for k, v in mi.items() if k.endswith("context_length") and isinstance(v, int)),
                              default=CLOUD_CTX)
                    info = ("ollama", top, 1)
                else:
                    r = await client.get(f"{root}/props")
                    if r.status_code == 200:
                        gen = r.json().get("default_generation_settings") or {}
                        n_ctx, slots = int(gen.get("n_ctx") or 0), int(r.json().get("total_slots") or 0)
                        if not n_ctx:
                            args = next((m.get("status", {}).get("args", []) for m in await models()
                                         if m.get("id") == model), [])
                            if "--ctx-size" in args:
                                n_ctx = int(args[args.index("--ctx-size") + 1])
                            if "--parallel" in args:
                                slots = int(args[args.index("--parallel") + 1])
                        info = ("llamacpp", n_ctx or 8192, max(slots, 1))
                    else:  # API ngoài: OpenRouter trả context_length cho từng model
                        m = next((m for m in await models() if m.get("id") == model), {})
                        ctx = next((int(m[k]) for k in ("context_length", "max_context_length", "max_model_len")
                                    if isinstance(m.get(k), int)), CLOUD_CTX)
                        info = ("other", ctx, CLOUD_SLOTS)
            except (httpx.HTTPError, KeyError, ValueError, TypeError, StopIteration):
                pass
        _servers[key] = info
        print(f"[llm] {base_url} ({model}): {info[0]}, ngữ cảnh {info[1]} token, {info[2]} lượt song song")
    return _servers[key]


_llm_sems: dict[tuple, asyncio.Semaphore] = {}


def llm_sem(base_url: str, slots: int) -> asyncio.Semaphore:
    """Không gửi quá số slot của server: llama.cpp/Ollama một slot thì request thứ hai chỉ nằm xếp hàng
    (và dễ quá thời gian chờ), trong khi trang vẫn hiện "đang dịch"."""
    key = (base_url, asyncio.get_running_loop())
    if key not in _llm_sems:
        _llm_sems[key] = asyncio.Semaphore(max(1, slots))
    return _llm_sems[key]


# Lượt dịch hiện tại (một trang): num_ctx của tài liệu + token đã dùng, để chat() đọc/ghi mà không phải truyền qua mọi hàm
_run: contextvars.ContextVar[dict] = contextvars.ContextVar("llm_run")
ITEMS_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["id", "text"], "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}}}}


def wait_for(cfg: "Config", max_out: int) -> float:
    """Thời gian chờ một lượt gọi. Gộp 2–3 trang làm mỗi lượt sinh ra nhiều chữ hơn hẳn, mà model chạy trên máy
    cá nhân chỉ vài token/giây (đo 20/09/2026: qwen3.5 8B trên M2 mất ~6 phút cho một lô 2 trang) — để nguyên 15
    phút là công dịch cả nhóm mất trắng vì quá hạn. API cloud sinh nhanh nên giữ mức cũ."""
    if not is_self_hosted(cfg.base_url):
        return 900.0
    return max(900.0, max_out / 3)   # ~3 token/giây là mức chậm nhất còn đáng chờ


async def chat(cfg: Config, system: str, payload: dict, max_out: int = 0, est_in: int = 0) -> dict[int, str]:
    """`max_out`: trần token đầu ra (chặn model lặp vô hạn, chặn API ngoài sinh dài quá tốn tiền).
    `est_in`: số token đầu vào app ước lượng — server báo nhận ít hơn hẳn nghĩa là prompt đã bị cắt."""
    data = await chat_json(cfg, system, payload, ITEMS_SCHEMA, max_out, est_in)
    return {int(it["id"]): str(it["text"]) for it in data.get("items", []) if "id" in it and it.get("text")}


async def chat_json(cfg: Config, system: str, payload: dict, schema: dict, max_out: int = 0, est_in: int = 0,
                    extra: dict | None = None) -> dict:
    """Một lượt gọi LLM trả về JSON theo `schema` (Ollama và llama.cpp ép được khung này).
    `extra`: tham số thêm cho API kiểu OpenAI (không dùng với Ollama)."""
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    if "openrouter.ai" in cfg.base_url:
        headers["HTTP-Referer"] = "http://localhost"
        headers["X-Title"] = "PDF Paper Translator"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    run = _run.get(None)
    kind, _, slots = await server_info(cfg.base_url, cfg.model)
    if kind == "ollama":
        # API gốc của Ollama: API kiểu OpenAI bỏ qua num_ctx (luôn 4096); `format` = schema JSON → model không thể
        # trả JSON hỏng (trước đây hỏng là phải dịch lại từng đoạn)
        url = re.sub(r"/v1/?$", "", cfg.base_url.rstrip("/")) + "/api/chat"
        num_ctx = (run or {}).get("num_ctx") or 8192
        body = {"model": cfg.model, "messages": messages, "stream": False, "format": schema,
                "options": {"temperature": cfg.temperature, "num_ctx": num_ctx}}
        if max_out:
            body["options"]["num_predict"] = min(max_out, num_ctx)
        if cfg.disable_thinking:
            body["think"] = False
    else:
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        body = no_think(cfg, url, {"model": cfg.model, "temperature": cfg.temperature, "messages": messages})
        body.update(extra or {})
        if max_out:
            body["max_tokens"] = max_out
        if kind == "llamacpp":  # llama.cpp cũng ép được khung JSON; API ngoài thì không phải model nào cũng nhận
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "items", "strict": True, "schema": schema}}
    try:
        async with httpx.AsyncClient(timeout=wait_for(cfg, max_out)) as client, llm_sem(cfg.base_url, slots):
            r = await client.post(url, headers=headers, json=body)
            if r.status_code >= 400 and kind != "ollama" and (k := refused_param(body, r.status_code, r.text)):
                body.pop(k)                                               # model không nhận tham số này → hỏi lại
                if (r := await client.post(url, headers=headers, json=body)).status_code < 400:
                    _REFUSED.setdefault((url, cfg.model), set()).add(k)
    except httpx.HTTPError as e:
        raise LLMError(f"Không kết nối được LLM tại {url}: {e!r}")
    if r.status_code >= 400:
        raise LLMError(f"Lỗi LLM {r.status_code}: {r.text[:500]}")
    res = r.json()
    if "error" in res and not res.get("choices") and not res.get("message"):
        # OpenRouter trả HTTP 200 kèm {"error": …} khi nhà cung cấp quá tải (model free hay gặp)
        err = res["error"]
        raise LLMError(f"Lỗi LLM: {err.get('message', err) if isinstance(err, dict) else err}")
    if kind == "ollama":
        content, used = res["message"]["content"] or "", (res.get("prompt_eval_count", 0), res.get("eval_count", 0))
    else:
        content = res["choices"][0]["message"]["content"] or ""
        used = ((res.get("usage") or {}).get("prompt_tokens", 0), (res.get("usage") or {}).get("completion_tokens", 0))
    if run is not None:
        t = run["tokens"]
        t["calls"] += 1
        t["prompt"] += used[0]
        t["completion"] += used[1]
        t["max_call"] = max(t["max_call"], used[0] + used[1])
    if est_in and used[0] and used[0] < 0.8 * est_in:
        raise PromptCut(f"server chỉ nhận {used[0]} token trong khi gửi ~{est_in} (prompt bị cắt)")
    return parse_json(content)


def max_len(text: str, cfg: Config, tight: bool = False) -> int:
    """Giới hạn ký tự bản dịch ≈ ratio × chỗ bản gốc chiếm trong khung: chữ CJK rộng bằng ~2 chữ Latin.
    `tight` (ô bảng): ô hẹp, hai bên đã có ô khác nên không nới được → không cho dài hơn bản gốc."""
    text = untag(text)
    ratio = min(cfg.length_ratio, 1.0) if tight else cfg.length_ratio
    return max(20, int((len(text) + len(_CJK.findall(text))) * ratio))


def keep_formulas(items: list[dict], out: dict[int, str]) -> dict[int, str]:
    """Chuẩn hoá {v1} trong bản dịch ({ v1 }, {V1} → {v1}); bỏ bản dịch làm mất công thức nào đó để đoạn đó
    được dịch lại / dùng Google / giữ nguyên bản gốc, thay vì in ra bản dịch thiếu công thức.
    Thẻ <b>/<i> thiếu hoặc lồng sai thì chỉ bỏ thẻ (mất đậm/nghiêng giữa đoạn, không mất bản dịch)."""
    src = {it["id"]: it["text"] for it in items}
    ok = {}
    for i, t in out.items():
        t = PLACEHOLDER.sub(lambda m: "{v%s}" % int(m[1]), t)
        if set(PLACEHOLDER.findall(src.get(i, ""))) <= set(PLACEHOLDER.findall(t)):
            ok[i] = keep_tags(src.get(i, ""), t)
    return ok


_STAMP = re.compile(r"\s*(arXiv:\S+|Preprint|Published as a conference paper.*|Under review.*|Proceedings of.*)\s*",
                    re.I)


def doc_title(doc: pymupdf.Document) -> str:
    """Tên bài: metadata nếu có, không thì dòng chữ to nhất ở trang đầu.
    Bỏ chữ xoay dọc ở lề: con dấu "arXiv:1810.04805v2 [cs.CL] 24 May 2019" in cỡ chữ to hơn cả tiêu đề, nên
    trước đây MỌI bài arXiv đều lấy nhầm con dấu này làm tên bài (rồi đưa vào prompt dịch và bảng thuật ngữ)."""
    title = (doc.metadata or {}).get("title") or ""
    if len(title.strip()) >= 10 and not _STAMP.fullmatch(title):
        return title.strip()[:200]
    lines = []
    for b in doc[0].get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]:
        for ln in b.get("lines", []):
            if abs(ln["dir"][1]) > 0.1:   # dòng xoay dọc (con dấu bên lề)
                continue
            text = "".join(sp["text"] for sp in ln["spans"]).strip()
            if len(text) >= 3 and not _STAMP.fullmatch(text):
                lines.append((max(sp["size"] for sp in ln["spans"]), ln["bbox"][1], text))
    if not lines:
        return ""
    # Cả DÒNG chứ không phải từng span, và gộp các dòng liền nhau cùng cỡ chữ lớn nhất: tiêu đề dài nằm trên
    # hai dòng ("Retrieval-Augmented Generation for / Knowledge-Intensive NLP Tasks"), còn tiêu đề in hoa nhỏ
    # thì chữ cái đầu là span to riêng ("T" + "RANSFORMER") nên lấy span sẽ ra mảnh chữ vô nghĩa.
    top = max(s for s, _, _ in lines)
    title, last = "", None
    for size, y, text in sorted(lines, key=lambda x: x[1]):
        if size < top - 0.6:
            continue
        if last is not None and y - last > 3 * top:   # cách quá xa → không cùng tiêu đề
            break
        title, last = (title + " " + text).strip(), y
    return title[:200]


_REFS_HEAD = re.compile(r"(?:\d+\.?|[IVX]+\.)?\s*(?:references|bibliography|literature cited)", re.I)
_APPX_HEAD = re.compile(r"(?:appendix|appendices|supplementary material)\b|[A-H](?:\.\d+)?\s+[A-Z][a-z]", re.I)
_CAPTION = re.compile(r"(?:figure|fig\.|table)\s*[A-Z]?\d+\s*[:.]", re.I)


def block_style(b: dict) -> tuple[float, bool]:
    """(cỡ chữ, in đậm?) của span đầu tiên có chữ trong khối — để nhận ra tiêu đề mục."""
    for ln in b.get("lines", []):
        for sp in ln["spans"]:
            if sp["text"].strip():
                return sp["size"], bool(sp["flags"] & 16 or _BOLD.search(sp["font"]))
    return 0.0, False


class RefsScan:
    """Đi qua các khối chữ theo thứ tự đọc, cho biết khối nào thuộc phần Tài liệu tham khảo: từ tiêu đề References tới
    mục kế tiếp — tiêu đề Phụ lục ("Appendix", "A  Details…"), tiêu đề cùng kiểu chữ với tiêu đề References (in đậm,
    không nhỏ hơn: phụ lục không đánh số như "Attention Visualizations" của bài Attention), hay chú thích hình/bảng —
    hoặc tới hết bài."""

    def __init__(self):
        self.on, self.head = False, None

    def step(self, first: str, style: tuple[float, bool]) -> str:
        """"head" (tiêu đề References), "ref" (khối trong danh mục) hay "" (không thuộc)."""
        if len(first) < 40 and _REFS_HEAD.fullmatch(first):
            self.on, self.head = True, style
            return "head"
        if self.on and len(first) < 80 and (_APPX_HEAD.match(first) or _CAPTION.match(first) or (
                style[1] and self.head and self.head[1] and style[0] >= self.head[0] - 0.5
                and not re.search(r"\b(?:19|20)\d\d\b", first))):
            self.on = False
        return "ref" if self.on else ""
_TOK = re.compile(r"[A-Za-z0-9]+(?:[-.'][A-Za-z0-9]+)*")
# Tên riêng/ký hiệu: có chữ hoa ở giữa (RoPE, FlashAttention), hai chữ hoa liền (GQA, LLMs), chữ đi với số (A100, GPT-3.5)
_SYM = re.compile(r"[A-Za-z]*(?:[a-z][A-Z]|[A-Z]{2}|[A-Za-z]\d)[A-Za-z0-9.\-]*")
_CHUNK = re.compile(r"[,;:!?()\[\]{}\"“”‘’=<>+*/|%#&@$^~`\\–—]|\.(?=\s|$)|\n| - ")


def _singular(w: str) -> str:
    if len(w) > 5 and w.endswith(("trices", "dices", "tices")):   # matrices, indices, vertices
        return w[:-4] + ("x" if w.endswith("trices") else "ex")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is")) and not (len(w) > 4 and w.endswith("us")):
        return w[:-1]
    return w


def _edge(tok: str) -> bool:
    low = tok.lower()
    return low == "of" or low in _EDGE or _singular(low) in _EDGE


def _term_key(words: list[str]) -> str:
    """Khoá gộp các cách viết của một thuật ngữ: chữ thường, chữ cuối về số ít (models = model, LLMs = LLM,
    matrices = matrix)."""
    ws = [w.lower() for w in words]
    ws[-1] = _singular(ws[-1])
    return " ".join(ws)


class PaperText(NamedTuple):
    body: str            # thân bài, mỗi đoạn một dòng
    refs: str            # tài liệu tham khảo
    heads: list[str]     # tiêu đề mục ("3.2 Multi-Head Attention")
    italic: set[str]     # khoá (_term_key) của các cụm in nghiêng — bài báo hay in nghiêng thuật ngữ mới
    pages: list[str]     # thân bài theo từng trang, để đo thuật ngữ rải khắp bài hay dồn một chỗ


_ITALIC = re.compile(r"Ital|Obli|CMTI|-It\b|\.I$", re.I)
_BOLD = re.compile(r"Bold|CMBX|\.B$|-Bd\b|Semibold|Heavy|Black", re.I)
_HEAD = re.compile(r"(?:\d+(?:\.\d+)*|[A-H](?:\.\d+)*)\.?\s+[A-Z]")


def paper_text(doc: pymupdf.Document) -> PaperText:
    """Chữ để rút thuật ngữ. Bỏ tên tác giả/đơn vị trước Abstract ở trang đầu, chữ xoay dọc bên lề, chữ trong
    hình/bảng (khối < 6 từ hoặc toàn số) và chữ in bằng font công thức (thay bằng "|" để ký hiệu không dính vào cụm
    từ). Phụ lục nằm sau tài liệu tham khảo vẫn tính là thân bài."""
    keep = hyphen_rule(doc)
    blocks = []
    for pno, page in enumerate(doc):
        for b in page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]:
            lines, spans, italic = [], [], set()
            for ln in b.get("lines", []):
                if abs(ln["dir"][1]) > 0.1:
                    continue
                lines.append("".join(" | " if MATH_FONT.search(sp["font"]) else sp["text"] for sp in ln["spans"]))
                spans += [sp for sp in ln["spans"] if sp["text"].strip() and not MATH_FONT.search(sp["font"])]
                run: list[str] = []      # các span in nghiêng liền nhau trên dòng = một cụm
                for sp in [*ln["spans"], None]:
                    if sp and not MATH_FONT.search(sp["font"]) and (sp["flags"] & 2 or _ITALIC.search(sp["font"])):
                        run.append(sp["text"])
                    elif run:
                        toks = _TOK.findall(" ".join(run))
                        if 1 <= len(toks) <= 5 and any(len(t) > 2 for t in toks):
                            italic.add(_term_key(toks))
                        run = []
            if text := join_lines(lines, keep).strip():
                bold = bool(spans) and all(sp["flags"] & 16 or _BOLD.search(sp["font"]) for sp in spans)
                blocks.append((pno, text, lines[0].strip(), bold, italic, block_style(b)))
    start = next((i for i, (p, t, *_) in enumerate(blocks) if p == 0 and re.match(r"abstract\b", t, re.I)), 0)
    body, refs, heads, italic, pages, scan = [], [], [], set(), collections.defaultdict(list), RefsScan()
    for pno, text, first, bold, it, style in blocks[start:]:
        if k := scan.step(first, style):
            if k == "head":
                text = text[len(first):].strip()
            refs.append(text)
            continue
        toks = _TOK.findall(text)
        if len(toks) >= 6 and sum(t[0].isdigit() for t in toks) < 0.3 * len(toks) \
                and sum(t.lower() in _STOP for t in toks) >= 0.1 * len(toks):
            body.append(text)
            pages[pno].append(text)
            italic |= it
        elif 1 <= len(toks) <= 12 and len(text) < 100 and "|" not in text and (bold or _HEAD.match(text)):
            heads.append(text)
    return PaperText("\n".join(body), "\n".join(refs), heads, italic, ["\n".join(v) for v in pages.values()])


def _long_form(sf: str, before: str) -> str | None:
    """Dạng đầy đủ của chữ viết tắt `sf` ngay trước "(sf)" (thuật toán Schwartz–Hearst): khớp từng chữ của `sf` từ
    phải sang trái vào `before`, chữ đầu tiên phải là đầu một từ."""
    words = _TOK.findall(re.split(r"[.;:,(\[\n]", before)[-1])[-min(len(sf) + 5, 2 * len(sf)):]
    cand = " ".join(words)
    s, i = len(sf) - 1, len(cand) - 1
    while s >= 0:
        c = sf[s].lower()
        if not c.isalnum():
            s -= 1
            continue
        while i >= 0 and (cand[i].lower() != c or (s == 0 and i > 0 and cand[i - 1].isalnum())):
            i -= 1
        if i < 0:
            return None
        i, s = i - 1, s - 1
    lf = cand[cand.rfind(" ", 0, i + 1) + 1:]
    return lf if len(lf) > len(sf) + 2 and " " in lf.replace("-", " ") and lf.split()[0].lower() not in _STOP else None


def term_candidates(doc: pymupdf.Document, limit: int = 80, text: PaperText | None = None,
                    loose: bool = False, features: bool = False) -> list[dict]:
    """Ứng viên thuật ngữ của bài, xếp theo độ đáng tin: [{"term", "n" (số lần), "kind", "ctx" (câu đầu tiên có nó),
    "abbr"/"long" (cặp viết tắt ↔ dạng đầy đủ)}]. Ưu tiên bắt đủ; bước LLM sau đó mới lọc.
      abbr  định nghĩa viết tắt trong bài: "Rotary Position Embedding (RoPE)" → cả hai dạng
      sym   tên riêng/viết tắt: GQA, RoPE, FlashAttention-2, A100
      phrase cụm 2–4 từ lặp ≥ 3 lần (multi-head attention, target data); cụm gần như chỉ xuất hiện bên trong một cụm
            dài hơn thì bỏ (scaled dot-product ⊂ scaled dot-product attention)
      word  từ đơn không phổ thông lặp ≥ 5 lần; điểm tính trên số lần xuất hiện NGOÀI các cụm đã chọn, để "relative"
            (hầu như chỉ trong "relative position") không lọt vào
    Tên người (đứng trước "et al", hoặc gặp trong tài liệu tham khảo nhiều hơn trong bài) bị bỏ.
    `text`: kết quả paper_text(doc) nếu đã có.
    `loose`: ngưỡng nới để LLM đọc ngữ cảnh mà quyết — cụm/tên chỉ cần lặp 2 lần, từ đơn 3 lần, nhận cả từ đơn ngắn hay
    phổ thông (key, value, query, head: trong bài attention là thuật ngữ). Độ phủ của 150 ứng viên đầu: dev 92%,
    test 98% (chặt: 88/93% ở 80 ứng viên) — nhưng đuôi danh sách toàn rác, xem GLOSSARY_FIXED."""
    min_phrase, min_word = (2, 3) if loose else (3, 5)
    text = text or paper_text(doc)
    body, refs = text.body, text.refs
    counts, surface = collections.Counter(), collections.defaultdict(collections.Counter)
    supers = collections.defaultdict(set)   # cụm → các cụm dài hơn một từ chứa nó
    for chunk in _CHUNK.split(body):
        run: list[str] = []
        for tok in [*_TOK.findall(chunk), ""]:
            low = tok.lower()
            breaks = not tok or (low in _STOP and low != "of") or (len(tok) <= 2 and tok.isalpha() and tok.islower()) \
                or (len(tok) == 1 and not tok.isdigit())
            if not breaks:
                run.append(tok)
                continue
            for i in range(len(run)):
                for n in range(1, 4):
                    w = run[i:i + n]
                    if len(w) < n or _edge(w[0]) or _edge(w[-1]) or w[0][0].isdigit() \
                            or any(t[0].isdigit() and not p[0].isupper() for p, t in zip(w, w[1:])):
                        continue    # số chỉ được đứng sau tên riêng: Llama 2, Mixtral 8x7B (không: head dimension 64)
                    if n == 3 and all(_SYM.fullmatch(t) for t in w) or len({t.lower() for t in w}) < n:
                        continue    # hàng/cột bảng: "FPR FNR FPR", "Arc-c HellaS MMLU"
                    k = _term_key(w)
                    counts[k] += 1
                    surface[k][" ".join(w)] += 1
                    if n >= 2:
                        supers[_term_key(w[:-1])].add(k)
                        supers[_term_key(w[1:])].add(k)
            run = []

    names = {n.lower() for n in re.findall(r"\b([A-Z][a-z]+)(?:\s+and\s+[A-Z][a-z]+)?\s+et\s+al\b", body)}
    abbr: dict[str, str] = {}
    for m in re.finditer(r"\(([A-Za-z][A-Za-z0-9\-]{1,9})\)", body):
        sf = m[1]
        if sum(c.isupper() for c in sf) < 1 or sf.lower() in _STOP:
            continue
        if sf.endswith("s") and sum(c.isupper() for c in sf[:-1]) >= 2:
            sf = sf[:-1]
        if (lf := _long_form(sf, body[max(0, m.start() - 200):m.start()])) and sf not in abbr:
            abbr[sf] = lf

    def refs_heavy(k: str) -> bool:
        pat = re.compile(r"\b" + re.escape(k) + r"s?\b", re.I)
        return len(pat.findall(refs)) > max(2, 3 * counts[k])

    found: dict[str, dict] = {}

    def add(k: str, kind: str, score: float, term: str | None = None):
        if k not in found or found[k]["score"] < score:
            found[k] = {"term": term or surface[k].most_common(1)[0][0], "n": counts[k], "kind": kind, "score": score,
                        "key": k}

    for sf, lf in abbr.items():
        ks, kl = _term_key([sf]), _term_key(lf.split())
        n = counts[ks] + counts[kl]
        if n >= 3:
            add(kl, "abbr", 1000 + n, lf if counts[kl] == 0 else None)
            add(ks, "abbr", 1000 + n, sf)
            found[kl]["abbr"], found[ks]["long"] = found[ks]["term"], found[kl]["term"]
            found[kl]["n"] = found[ks]["n"] = n   # hai dạng của một thuật ngữ: bài hay chỉ viết dạng tắt
    kept_phrases = set()
    for k, n in counts.items():
        words = k.split()
        term = surface[k].most_common(1)[0][0]
        if len(words) >= 2:
            # Mảnh của cụm dài hơn: "scaled dot-product" ⊂ "scaled dot-product attention". Nhưng cụm dài chỉ là
            # cụm này + một danh từ chung ("prompt injection" + attack) thì cụm này mới là thuật ngữ.
            top = max(supers[k], key=lambda s: counts[s], default=None)
            inside = counts[top] if top else 0
            fragment = inside >= 0.95 * n or inside >= 0.85 * n and not (
                top.startswith(k + " ") and top.split()[-1] in _WEAK | {_singular(w) for w in _WEAK})
            if n >= min_phrase and not fragment:
                kept_phrases.add(k)
                add(k, "phrase", n * (1 + 0.5 * (len(words) - 1)))
        elif _SYM.fullmatch(term) and not term.isdigit():
            caps = sum(c.isupper() for c in term)
            if n >= min_phrase and term.upper() not in _CAPS_STOP and (len(term) >= 3 or caps == 2 and n >= 5
                                                             or term[1].isdigit()) \
                    and not (caps == 1 and term[0].islower()):          # dV, mW, qW: ký hiệu toán
                add(k, "sym", 2 * n)
    for k, n in counts.items():
        term = surface[k].most_common(1)[0][0]
        if " " in k or k in found or len(k) < (3 if loose else 4) or k in _STOP or k in _WEAK and not loose \
                or _edge(k) or k in names or not re.fullmatch(r"[a-z][a-z\-]*[a-z]", k) or k.endswith(("ly", "ed")) \
                or n < min_word:
            continue
        rest = n - sum(counts[s] for s in supers[k] if s in kept_phrases)
        cap = sum(c for s, c in surface[k].items() if s[0].isupper()) / n
        if rest >= min_word and rest >= 0.35 * n:
            add(k, "word", rest * (1.2 if cap > 0.8 else 0.6))
    out = sorted((c for c in found.values() if c["kind"] == "abbr" or not refs_heavy(c["key"])),
                 key=lambda c: -c["score"])
    if features:   # cho scripts/fit_term_ranker.py (thử nghiệm xếp hạng bằng mô hình học, chưa dùng trong app)
        for c in out:
            c["f"] = _term_features(c, text, doc_title(doc), surface[c["key"]])
    seen: set[str] = set()   # gạch nối = dấu cách: T5-XXL / T5 XXL, GPT-3.5-Turbo / GPT-3.5 Turbo → giữ cái xếp trên
    out = [c for c in out if not ((h := c["key"].replace("-", " ")) in seen or seen.add(h))][:limit]
    for c in out:
        m = re.search(r"(?<![\w-])" + re.escape(c["term"]) + r"s?(?![\w-])", body, re.I)
        if m:   # câu chứa thuật ngữ, dài quá thì lấy ~90 ký tự mỗi bên (không cắt giữa từ)
            dot = body.rfind(". ", 0, m.start())
            a = max(dot + 2 if dot >= 0 else 0, body.rfind("\n", 0, m.start()) + 1)
            e = min([x for x in (body.find(". ", m.end()), body.find("\n", m.end())) if x >= 0] or [len(body)])
            if a < m.start() - 90:
                a = body.find(" ", m.start() - 90) + 1
            if e > m.end() + 90:
                e = body.rfind(" ", 0, m.end() + 90)
            c["ctx"] = re.sub(r"\s*\|\s*", " ", body[a:e]).strip()
        if not features:
            del c["key"], c["score"]
    return out


_DEFINE = (r"(?:called|termed|refer(?:red)? to as|dubbed|named|denoted by|known as|we (?:propose|introduce|present|call|"
           r"define|name))\s+(?:(?:a|an|the|our|this)\s+)?(?:(?:new|novel|simple|general)\s+)?")
_zipf: dict[str, float] = {}


def _word_zipf(w: str) -> float:
    """Độ phổ biến của một từ trong tiếng Anh chung (thang Zipf, resources/english_zipf.tsv): the = 7,7; model = 5,1;
    dataset = 3,0; từ không có trong bảng (softmax, uptraining) = 2,5; tên riêng/ký hiệu (RoPE, A100) = 1,5."""
    if not _zipf:
        try:
            for ln in (BASE / "resources" / "english_zipf.tsv").read_text().splitlines():
                if ln and not ln.startswith("#"):
                    word, z = ln.split("\t")
                    _zipf[word] = float(z)
        except OSError:
            _zipf[""] = 0.0   # thiếu file: coi mọi từ là hiếm, không đọc lại
    if _SYM.fullmatch(w) or any(ch.isdigit() for ch in w):
        return 1.5
    low = w.lower()
    return _zipf.get(low, _zipf.get(_singular(low), 2.5))


def _term_features(c: dict, text: "PaperText", title: str, surface: collections.Counter) -> dict[str, float]:
    """Đặc trưng của một ứng viên cho thử nghiệm xếp hạng bằng hồi quy logistic (scripts/fit_term_ranker.py). Đo
    21/09/2026 trên 6 bài: không hơn xếp theo tần suất (AUC dev 0,85 → 0,85, test 0,86 → 0,93 nhưng độ phủ top 40
    không đổi) nên app chưa dùng — cần nhiều bài có nhãn hơn, vd. từ thuật ngữ người dùng xoá/thêm (user_terms)."""
    term = c["term"]
    pat = re.compile(r"(?<![\w-])" + re.escape(term) + r"s?(?![\w-])", re.I)
    words = [w for w in term.replace("-", " ").split() if w.lower() not in _STOP]
    zs = [_word_zipf(w) for w in words] or [2.5]
    n_surf = sum(surface.values())
    return {
        # số lần trên 1.000 từ chứ không phải số lần: bài dài thì cái gì cũng lặp nhiều (DataSentinel 13.000 từ)
        "logn": math.log1p(1000 * c["n"] / max(1000, len(text.body.split()))),
        "abbr": float(c["kind"] == "abbr"), "sym": float(c["kind"] == "sym"),
        "phrase": float(c["kind"] == "phrase"), "word": float(c["kind"] == "word"),
        "nwords": float(min(len(words), 3)),
        "title": float(bool(pat.search(title))),
        "abstract": float(bool(pat.search(text.body[:1500]))),
        "head": float(any(pat.search(h) for h in text.heads)),
        "italic": float(c["key"] in text.italic),
        "defined": float(bool(re.search(_DEFINE + re.escape(term), text.body, re.I))),
        "spread": sum(bool(pat.search(p)) for p in text.pages) / max(1, len(text.pages)),
        "zmin": min(zs), "zmax": max(zs),
        "cap": sum(v for k, v in surface.items() if k[:1].isupper()) / n_surf if n_surf else 1.0,
    }




def glossary_terms(doc: pymupdf.Document, limit: int = 40) -> list[str]:
    """Thuật ngữ đáng thống nhất cả bài (các ứng viên đứng đầu của term_candidates)."""
    return [c["term"] for c in term_candidates(doc, limit)]


async def build_glossary(cfg: Config, doc: pymupdf.Document) -> dict | None:
    """Một lượt gọi LLM cho cả tài liệu → bảng thuật ngữ để người dùng xem lại trên giao diện:
    {"rows": [{"term": gốc, "vi": cách dịch, "keep": giữ nguyên tiếng Anh, "use": có đưa vào prompt không}],
     "extra": [{"term", "n", "hidden"}]} — extra là ứng viên LLM không chọn (hiện thành "gợi ý khác" để người dùng tự
    thêm) và ứng viên bị ẩn vì người dùng từng xoá (hidden).
    Code gom ~80 ứng viên (ưu tiên bắt đủ); LLM đọc câu ngữ cảnh của từng ứng viên để xếp loại (tên riêng / thuật
    ngữ / không phải thuật ngữ) và dịch thuật ngữ đúng nghĩa trong bài; gloss_rows kiểm lại bằng luật.
    Loại là một trường riêng chứ không phải "trả chữ gốc = giữ": model nhỏ (4b) lẫn "giữ ứng viên" với "giữ nguyên
    tiếng Anh" và chép lại nguyên văn mọi ứng viên (đo 21/09/2026).
    Trả None khi gọi LLM lỗi (vẫn dịch bình thường, và trang sau sẽ thử lập lại)."""
    text = await asyncio.to_thread(paper_text, doc)
    rows = GLOSSARY_ROWS
    pool = await asyncio.to_thread(term_candidates, doc, GLOSSARY_FIXED + GLOSSARY_SUGGEST, text)
    ut = user_terms()
    key = lambda c: _term_key(c["term"].split())
    hidden = [c for c in pool if key(c) in ut["blocked"]]
    pool = [c for c in pool if key(c) not in ut["blocked"]]
    cands, later = pool[:GLOSSARY_FIXED], pool[GLOSSARY_FIXED:]
    as_extra = lambda cs, hid=False: [{"term": c["term"], "n": c["n"], "hidden": hid} for c in cs]
    if not cands:
        return {"rows": taught_rows([], text.body), "extra": as_extra(hidden, True)}
    items = [{"id": i, "term": c["term"], "n": c["n"], **({"ctx": c["ctx"]} if c.get("ctx") else {})}
             for i, c in enumerate(cands)]
    abstract = re.sub(r"^abstract\W*", "", text[0][:700], flags=re.I).rsplit(" ", 1)[0]
    payload = {"paper": doc_title(doc), "abstract": abstract, "items": items}
    max_out = int(2.5 * sum(est_tokens(c["term"]) for c in cands)) + 20 * len(cands) + 256
    tries = [gloss_cfg(cfg)] + ([cfg] if cfg.gloss_model or cfg.gloss_base_url else [])
    if not is_self_hosted(tries[0].base_url):
        tries.insert(1, tries[0])   # API cloud (free) hay quá tải tạm thời: thử lại một lần trước khi lùi
    for i, c in enumerate(tries):   # model lập bảng lỗi → lùi về model dịch
        if i and c is tries[i - 1]:
            await asyncio.sleep(5)
        try:
            # Model suy luận trên OpenRouter (Nemotron Ultra) mặc định nghĩ ~5.000 token: 225 giây cho một bảng, tắt
            # đi (no_think) còn 13 giây mà vẫn lọc/dịch tốt (đo 21/09/2026)
            data = await chat_json(c, GLOSSARY_PROMPT.replace("{max_terms}", str(rows)), payload, GLOSSARY_SCHEMA,
                                   max_out)
            out = {int(it["id"]): (str(it["kind"]).strip().lower(), str(it.get("vi") or ""))
                   for it in data["items"] if "kind" in it}
            if not out:   # JSON hỏng, parse_json chỉ vớt được dạng {id, text} của lượt dịch
                raise ValueError("bảng thuật ngữ không có trường kind")
            break
        except (LLMError, ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
            print(f"[glossary] {c.model}: không lập được bảng thuật ngữ ({e!r})"
                  + (" → thử lại" if i + 1 < len(tries) else " → dịch không có bảng"))
    else:
        return None
    table = taught_rows(gloss_rows(cands, out)[:rows], text.body)
    kept = {_term_key(r["term"].split()) for r in table}
    return {"rows": table, "extra": as_extra([c for c in cands + later if key(c) not in kept]) + as_extra(hidden, True)}


def taught_rows(rows: list[dict], body: str) -> list[dict]:
    """Áp cách dịch người dùng đã dạy (user_terms prefer) lên bảng LLM lập, và thêm thuật ngữ đã dạy có trong bài
    (≥ 2 lần) mà LLM không chọn — không tính vào trần GLOSSARY_ROWS vì người dùng đã khẳng định là thuật ngữ."""
    prefer = user_terms()["prefer"]
    have = set()
    for r in rows:
        k = _term_key(r["term"].split())
        have.add(k)
        if k in prefer:
            r.update(vi=prefer[k]["vi"], keep=prefer[k]["keep"], use=True)
    for k, p in prefer.items():
        if k not in have and len(re.findall(r"(?<![\w-])" + re.escape(p["term"]) + r"s?(?![\w-])", body, re.I)) >= 2:
            rows.append({"term": p["term"], "vi": p["vi"], "keep": p["keep"], "use": True})
    return rows


def gloss_rows(cands: list[dict], out: dict[int, tuple[str, str]]) -> list[dict]:
    """Kiểm lại câu trả lời của LLM bằng luật, vì mỗi hàng sai sẽ bị áp ở mọi trang:
    - LLM xếp "other" (hay không trả về) → không phải thuật ngữ, bỏ; "name" → giữ nguyên tiếng Anh;
    - tên riêng/viết tắt một chữ (GQA, RoPE, FlashAttention-2, A100) luôn giữ nguyên, kể cả khi LLM lỡ dịch;
    - bỏ lời giải thích trong ngoặc; dạng đầy đủ của chữ viết tắt thì ghi kèm chữ viết tắt: "nhúng vị trí xoay (RoPE)";
    - bản dịch dài bất thường (LLM giải thích thay vì dịch), hoặc lại là một ứng viên tiếng Anh khác
      ("dot-product attention" → "scaled dot-product attention"), thì bỏ hàng đó;
    - hai thuật ngữ khác nhau cùng một cách dịch ("position encoding", "position embedding" → "nhúng vị trí") xoá mất
      khác biệt giữa chúng → hàng xếp sau vẫn hiện trên giao diện nhưng bỏ tích "Dùng" để người dùng tự quyết."""
    rows, used, english = [], {}, {_term_key(c["term"].split()) for c in cands}
    for i, c in enumerate(cands):
        kind, vi = out.get(i, ("other", ""))
        kind = kind if kind in ("name", "term") else "other"
        term, vi = c["term"], vi.strip()
        if kind == "other" or kind == "term" and not vi:
            continue
        if kind == "name":
            vi = term
        if " " not in term and _SYM.fullmatch(term):
            vi = term
        vi = re.sub(r"\s*\([^)]*\)", "", vi).strip()
        if not vi or len(vi) > 3 * len(term) + 15:
            continue
        key, vkey = _term_key(term.split()), _term_key(vi.split())
        if vkey != key and vkey in english:
            continue
        if (sf := c.get("abbr")) and vi.lower() != term.lower():
            vi = f"{vi} ({sf})"
        keep = vi.lower() == term.lower()
        clash = not keep and used.get(vkey, key) != key       # cùng cách dịch với thuật ngữ khác (không tính số nhiều)
        used.setdefault(vkey, key)
        rows.append({"term": term, "vi": vi, "keep": keep, "use": not clash})
    return rows


def apply_gloss(d: dict) -> None:
    """Từ bảng người dùng đã chỉnh trên giao diện → {thuật ngữ: chữ phải dùng} đưa vào prompt.
    Hàng "giữ nguyên" vẫn nằm trong bảng (ghi `term = term`) để model không tự dịch thuật ngữ đó."""
    d["glossary"] = {r["term"]: (r["term"] if r.get("keep") else r["vi"].strip()) for r in d.get("gloss", [])
                     if r.get("use", True) and (r.get("keep") or r["vi"].strip())}


def glossary_text(cfg: "Config", doc: dict | None = None) -> str:
    """Bảng thuật ngữ của tài liệu: bảng người dùng gõ tay trước (được ưu tiên), rồi bảng của bài (bỏ trùng)."""
    user = cfg.glossary.strip()
    known = {l.split("=")[0].strip().lower() for l in user.splitlines() if "=" in l}
    auto = "\n".join(f"{a} = {b}" for a, b in (doc or {}).get("glossary", {}).items() if a.lower() not in known)
    return "\n".join(x for x in (user, auto) if x)


def system_prompt(cfg: "Config", doc: dict | None = None) -> str:
    """Phần đầu prompt, GIỐNG NHAU ở mọi lượt gọi của một tài liệu: thuật ngữ thống nhất cả bài, và server
    (Ollama, llama.cpp, OpenAI…) tái dùng được KV cache đã xử lý của phần này."""
    parts, doc = [SYSTEM_PROMPT], doc or {}
    if doc.get("title"):
        parts.append(f'The paper is titled "{doc["title"]}".')
    if gloss := glossary_text(cfg, doc):
        parts.append("Glossary (must follow exactly):\n" + gloss)
    return "\n".join(parts)


async def llm_translate(cfg: Config, items: list[dict], system: str = "") -> dict[int, str]:
    system = system or system_prompt(cfg)
    payload = {"items": [{"id": it["id"], "max_len": max_len(it["text"], cfg, it.get("tight", False)),
                          "text": it["text"], **({"tight": True} if it.get("tight") else {}),
                          **{k: it[k] for k in ("prev", "next") if k in it}} for it in items]}
    est_in = est_tokens(system) + sum(in_tokens(it) for it in items)
    max_out = int(1.6 * sum(out_tokens(it) for it in items)) + 256
    return keep_formulas(items, await chat(cfg, system, payload, max_out, est_in))


async def llm_shorten(cfg: Config, src: dict[int, str], out: dict[int, str]) -> None:
    """Rút gọn các bản dịch vượt quá giới hạn độ dài (sửa trực tiếp `out`)."""
    too_long = [i for i, t in out.items() if i in src and len(untag(t)) > max_len(src[i], cfg) * 1.1]
    if not too_long:
        return
    payload = {"items": [{"id": i, "max_len": max_len(src[i], cfg), "source": src[i], "text": out[i]}
                         for i in too_long]}
    try:
        short = await chat(cfg, SHORTEN_PROMPT, payload,
                           int(1.6 * sum(out_tokens({"text": src[i]}) for i in too_long)) + 256)
    except (LLMError, ValueError, json.JSONDecodeError, KeyError):
        return
    for i, t in keep_formulas([{"id": i, "text": src[i]} for i in too_long], short).items():
        if i in out and len(untag(t)) < len(untag(out[i])):
            out[i] = t


_google_sem = asyncio.Semaphore(4)


async def google_translate_one(client: httpx.AsyncClient, text: str) -> str:
    async with _google_sem:
        for attempt in range(3):
            try:
                r = await client.post("https://translate.googleapis.com/translate_a/single",
                                      params={"client": "gtx", "sl": "auto", "tl": "vi", "dt": "t"},
                                      data={"q": text})
                if r.status_code == 200:
                    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1 + attempt)
    return ""


async def google_translate(items: list[dict]) -> dict[int, str]:
    async with httpx.AsyncClient(timeout=30) as client:
        res = await asyncio.gather(*(google_translate_one(client, it["text"]) for it in items))
    return keep_formulas(items, {it["id"]: t for it, t in zip(items, res) if t})


def mt_prompt(model: str, text: str, glossary: str) -> str:
    """Prompt chính thức của các model chuyên dịch (TranslateGemma, HY-MT); model khác dùng prompt chung."""
    m = model.lower()
    terms = [l.split("=", 1) for l in glossary.splitlines() if "=" in l]
    if "translategemma" in m:  # prompt cần đúng ngôn ngữ nguồn: có chữ Hán → tiếng Trung, còn lại → tiếng Anh
        src, code = ("Chinese", "zh") if re.search(r"[㐀-䶿一-鿿]", text) else ("English", "en")
        return (f"You are a professional {src} ({code}) to Vietnamese (vi) translator. Your goal is to accurately convey "
                f"the meaning and nuances of the original {src} text while adhering to Vietnamese grammar, vocabulary, "
                "and cultural sensitivities.\nProduce only the Vietnamese translation, without any additional "
                f"explanations or commentary. Please translate the following {src} text into Vietnamese:\n\n\n" + text)
    hint = "".join(f"{a.strip()} translated to {b.strip()}\n" for a, b in terms if a.strip().lower() in untag(text).lower())
    prefix = f"Refer to the following translation:\n{hint}\n" if hint else ""
    return prefix + "Translate the following segment into Vietnamese, without additional explanation.\n\n" + text


async def mt_translate(cfg: "Config", items: list[dict]) -> dict[int, str]:
    """Gửi từng đoạn một (model chuyên dịch không làm theo prompt JSON theo lô)."""
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    sem = asyncio.Semaphore(2)

    async def one(client: httpx.AsyncClient, it: dict):
        body = {"model": cfg.model, "temperature": cfg.temperature,
                "messages": [{"role": "user", "content": mt_prompt(cfg.model, it["text"], cfg.glossary)}]}
        async with sem:
            try:
                r = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                raise LLMError(f"Không kết nối được model dịch tại {url}: {e!r}")
        if r.status_code >= 400:
            raise LLMError(f"Lỗi model dịch {r.status_code}: {r.text[:300]}")
        text = re.sub(r"<think>.*?</think>", "", r.json()["choices"][0]["message"]["content"] or "", flags=re.S)
        return it["id"], text.strip()

    async with httpx.AsyncClient(timeout=600) as client:
        res = await asyncio.gather(*(one(client, it) for it in items))
    return keep_formulas(items, {i: t for i, t in res if t})


MAX_ITEMS = 40   # số đoạn tối đa một lượt gọi (model 9B nhận nhiều đoạn ngắn như ô bảng hay bỏ sót đoạn)


def make_batches(items: list[dict], budget: int, max_items: int = MAX_ITEMS) -> list[list[dict]]:
    """Lô gửi LLM: tổng token (vào + ra dự kiến) ≤ budget; tối đa `max_items` đoạn một lô."""
    batches, cur, size = [], [], 0
    for it in items:
        n = item_tokens(it)
        if cur and (size + n > budget or len(cur) >= max_items):
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    return batches + ([cur] if cur else [])


async def call_budget(cfg: Config, system: str) -> int:
    """Số token một lượt gọi được phép chiếm (vào + ra): 85% ngữ cảnh trừ phần đầu prompt."""
    kind, n_ctx, _ = await server_info(cfg.base_url, cfg.model)
    ctx = ((_run.get(None) or {}).get("num_ctx") or 8192) if kind == "ollama" else n_ctx
    return max(int(0.85 * ctx) - est_tokens(system), 500)


async def translate_blocks(cfg: Config, blocks: list[dict], context: dict[int, dict] | None = None,
                           system: str = "") -> tuple[dict[int, str], dict]:
    """`context`: {id: {"prev": ..., "next": ...}} cho đoạn là mảnh câu bị ngắt sang cột/trang khác (chỉ LLM dùng).
    Đoạn trùng chữ (nhãn hàng lặp lại ở mọi bảng, "FPR", "FNR"…) chỉ dịch một lần: đỡ token, dịch thống nhất."""
    items, dup, seen = [], {}, {}
    for i, b in enumerate(blocks):
        it = {"id": i, "text": b["text"], **({"tight": True} if b.get("label") == "table" else {}),
              **(context or {}).get(i, {})}
        if it["text"] in seen and not ("prev" in it or "next" in it):
            dup[i] = seen[it["text"]]
        else:
            seen.setdefault(it["text"], i)
            items.append(it)
    stats = {"llm": 0, "google": 0, "errors": []}
    result = await translate_items(cfg, items, stats, system)
    for i, j in dup.items():
        if j in result:
            result[i] = result[j]
    return result, stats


async def translate_items(cfg: Config, items: list[dict], stats: dict, system: str = "") -> dict[int, str]:
    if cfg.translator == "google":
        out = await google_translate(items)
        stats["google"] = len(out)
        return out
    if cfg.translator == "mt":
        try:
            result = await mt_translate(cfg, items)
        except LLMError as e:
            if not cfg.google_fallback:
                raise HTTPException(502, str(e))
            stats["errors"].append(str(e))
            result = {}
        stats["llm"] = len(result)
        missing = [it for it in items if it["id"] not in result]
        if missing and cfg.google_fallback:
            g = await google_translate(missing)
            result.update(g)
            stats["google"] = len(g)
        return result

    src = {it["id"]: it["text"] for it in items}
    result: dict[int, str] = {}
    system = system or system_prompt(cfg)
    queue = make_batches(items, await call_budget(cfg, system))
    while queue:
        batch = queue.pop(0)
        try:
            out = await llm_translate(cfg, batch, system)
        except PromptCut as e:  # server nhận thiếu → chia đôi lô rồi gửi lại
            if len(batch) > 1:
                queue[:0] = [batch[:len(batch) // 2], batch[len(batch) // 2:]]
                continue
            stats["errors"].append(str(e))
            out = {}
        except LLMError as e:  # lỗi kết nối/API → không thử lại từng đoạn
            stats["errors"].append(str(e))
            if not cfg.google_fallback:
                raise HTTPException(502, str(e))
            continue
        except (ValueError, json.JSONDecodeError, KeyError):
            out = {}
        result.update(out)
        for it in batch:  # model nhỏ hay làm hỏng JSON → thử lại từng đoạn
            if it["id"] not in result:
                try:
                    result.update(await llm_translate(cfg, [it], system))
                except LLMError as e:
                    stats["errors"].append(str(e))
                    break
                except (ValueError, json.JSONDecodeError, KeyError):
                    pass
    await llm_shorten(cfg, src, result)
    stats["llm"] = len(result)

    missing = [it for it in items if it["id"] not in result]
    if missing and cfg.google_fallback:
        g = await google_translate(missing)
        result.update(g)
        stats["google"] = len(g)
    return result


# ================================================================ API
@app.post("/api/upload")
async def upload(file: UploadFile):
    """Tải lên một PDF. Đúng file đã tải lên trước đây (cùng sha256) → trả lại tài liệu cũ, kèm `existing`: bản
    dịch, tô vàng, thuật ngữ vẫn còn, không phải dịch lại."""
    data = await file.read()
    sha = store.digest(data)
    if old := store.find(sha):
        d = get_doc(old)
        return {"id": old, "pages": d["out"].page_count, "name": d["name"], "existing": True,
                "scanned_pages": sum(not page_has_text(p) for p in pymupdf.open(d["src"]))}
    try:
        out = pymupdf.open("pdf", data)
    except Exception:
        raise HTTPException(400, "File PDF không hợp lệ")
    doc_id = uuid.uuid4().hex[:12]
    scanned = sum(not page_has_text(p) for p in out)
    DOCS[doc_id] = d = {"id": doc_id, "src": store.create(doc_id, data), "out": out, "done": set(),
                        "lock": asyncio.Lock(), "name": file.filename, "sha256": sha, "created": time.time(),
                        "blocks": {}, "placed": {}, "trans": {}, "marks": {}}
    store.save_state(d)
    return {"id": doc_id, "pages": out.page_count, "name": file.filename, "scanned_pages": scanned}


@app.get("/api/docs")
def list_docs():
    """Tài liệu đã lưu trên máy (mới dùng gần nhất trước) để mở lại mà không phải tải lên."""
    return {"docs": store.list_docs()}


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: str):
    """Xoá hẳn tài liệu: bản gốc, bản dịch, tô vàng, thuật ngữ. Không hoàn tác được."""
    d = DOCS.pop(doc_id, None)
    if not store.delete(doc_id) and d is None:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return {"deleted": doc_id}


@app.get("/api/{doc_id}/page/{n}/{kind}.png")
def page_png(doc_id: str, n: int, kind: str, zoom: float = 2.0):
    d = get_doc(doc_id)
    doc = d["out"] if kind == "translated" else pymupdf.open(d["src"])
    if not 0 <= n < doc.page_count:
        raise HTTPException(404)
    pix = doc[n].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return Response(pix.tobytes("png"), media_type="image/png", headers={"Cache-Control": "no-store"})


BLOCKS_VERSION = 2  # tăng khi đổi cách trích khối từ lớp chữ (2: vùng thuật toán/code tách theo dòng)


async def page_blocks(d: dict, n: int, cfg: Config, allow_ocr: bool = True,
                      fresh: bool = False) -> tuple[list[dict], bool] | None:
    """Khối của trang n, lấy lại từ lần trích trước nếu cấu hình OCR không đổi (OCR bằng VLM mất 30–70s/trang).
    Khoá kèm trạng thái model layout: lần trước lỗi phải gộp dòng theo khoảng cách thì lần này trích lại.
    allow_ocr=False (trang bên cạnh, chỉ để lấy ngữ cảnh): trang chưa trích mà cần OCR → None, không OCR thêm.
    fresh (sắp dịch cả trang): khối trích từ lớp chữ bằng code cũ hơn BLOCKS_VERSION thì trích lại — rẻ, không như OCR;
    trừ trang có vệt tô vàng (ghi theo số hiệu đoạn + vị trí ký tự; dấu ⛔ thì user_skips tự tìm lại theo chữ)."""
    key = (cfg.ocr_mode, cfg.ocr_engine, cfg.ocr_lang, cfg.ocr_vlm_base_url, cfg.ocr_vlm_model, cfg.ocr_custom_url)
    cached = d["blocks"].get(n)
    if cached and cached[0][0][:len(key)] == key and cached[0][1] == ("sess" in _layout):
        stale = cached[0][0][len(key):] != (BLOCKS_VERSION,) and not cached[2]
        if not (stale and fresh and not d["marks"].get(n)):
            return cached[1], cached[2]
    key += (BLOCKS_VERSION,)
    src_page = pymupdf.open(d["src"])[n]  # luôn trích từ bản gốc
    if not allow_ocr and needs_ocr(src_page, cfg):
        return None
    blocks, ocr = await get_blocks(src_page, cfg)
    d["blocks"][n] = ((key, "sess" in _layout), blocks, ocr)
    if ocr:                   # OCR đắt: lưu ngay, lượt dịch sau đó có lỗi thì khởi động lại vẫn không phải OCR lại
        store.save_state(d)
    return blocks, ocr


BODY = {"text", "abstract"}  # nhãn layout của đoạn thân bài (câu có thể bị ngắt sang cột/trang sau)
_SENT_END = re.compile(r"[.!?。！？:：;；…][\"'”’)\]]*$")


_SENT_BREAK = re.compile(r"[.!?](?=\s|$)|[。！？]")  # tiếng Trung: sau 。 thường không có dấu cách


def opens_mid(text: str) -> bool:
    """Đoạn mở đầu giữa câu: chữ đầu là chữ thường, hoặc chữ CJK (tiếng Trung không có chữ hoa để phân biệt)."""
    t = PLACEHOLDER.sub("", untag(text)).lstrip()
    return bool(t) and (t[0].islower() or bool(_CJK.match(t[0])))


def ends_open(text: str) -> bool:
    return not _SENT_END.search(PLACEHOLDER.sub("", untag(text)).rstrip())


def continues(prev: str, text: str) -> bool:
    """`text` nối tiếp câu dở của `prev` (câu bị ngắt sang cột/trang khác hoặc vòng qua hình)."""
    return ends_open(prev) and opens_mid(text)


def sentence_tail(text: str, n: int = 300) -> str:
    """Phần câu dở cuối đoạn (sau dấu kết câu cuối cùng); công thức của đoạn khác thay bằng … để khỏi lẫn {vN}."""
    t = PLACEHOLDER.sub("…", untag(text))
    ends = [m.end() for m in _SENT_BREAK.finditer(t)]
    start = max(ends[-1] if ends else 0, len(t) - n)
    if start == len(t) - n > 0 and " " in t[start:]:  # cắt theo độ dài → bắt đầu từ từ nguyên vẹn
        start = t.index(" ", start) + 1
    return t[start:].strip()


def sentence_head(text: str, n: int = 300) -> str:
    t = PLACEHOLDER.sub("…", untag(text))
    m = _SENT_BREAK.search(t)
    return t[:min(m.end() if m else len(t), n)].strip()


async def block_context(d: dict, pages: list[int], cfg: Config, flat: list[dict]) -> dict[int, dict]:
    """Ngữ cảnh cho đoạn thân bài bị ngắt giữa câu: "prev" = phần câu dở của đoạn trước, "next" = đầu đoạn sau.
    `flat` là khối của CẢ NHÓM trang nối liền nhau, nên câu ngắt giữa hai trang trong nhóm được nối ngay ở đây;
    chỉ hai đầu nhóm mới phải hỏi trang bên ngoài, và trang đó chỉ được trích nếu không phải OCR (hoặc đã trích sẵn)."""
    body = [i for i, b in enumerate(flat) if b.get("label") in BODY]
    if not body:
        return {}

    async def neighbour(m: int) -> list[dict]:
        if not 0 <= m < d["out"].page_count:
            return []
        got = await page_blocks(d, m, cfg, allow_ocr=False)
        return [b for b in got[0] if b.get("label") in BODY] if got else []

    ctx: dict[int, dict] = {}
    first, last = flat[body[0]], flat[body[-1]]
    # hỏi trang bên cạnh chỉ khi đầu/cuối nhóm có dấu hiệu câu bị ngắt (trích trang mất ~0,7s)
    if opens_mid(first["text"]) and (prev := await neighbour(pages[0] - 1)) and continues(prev[-1]["text"], first["text"]):
        ctx.setdefault(body[0], {})["prev"] = sentence_tail(prev[-1]["text"])
    if ends_open(last["text"]) and (nxt := await neighbour(pages[-1] + 1)) and continues(last["text"], nxt[0]["text"]):
        ctx.setdefault(body[-1], {})["next"] = sentence_head(nxt[0]["text"])
    for a, b in zip(body, body[1:]):  # câu ngắt từ cột trái sang cột phải, vòng qua hình/bảng, hoặc sang trang sau
        if continues(flat[a]["text"], flat[b]["text"]):
            ctx.setdefault(b, {})["prev"] = sentence_tail(flat[a]["text"])
            ctx.setdefault(a, {})["next"] = sentence_head(flat[b]["text"])
    return ctx


MAX_SPAN = 3   # tự động thì gộp tối đa 3 trang một lượt gọi


async def grow_group(d: dict, n: int, cfg: Config, span: int, system: str, got: dict, force: bool = False) -> list[int]:
    """Các trang gộp thêm vào cùng lượt gọi với trang n. Một nhóm = vừa đúng MỘT lượt gọi LLM: trang sau chỉ được
    thêm khi cả nhóm vẫn nằm trong ngữ cảnh và trong MAX_ITEMS đoạn. Ngữ cảnh lớn mà trang ngắn thì gộp được 2–3
    trang: câu ngắt qua trang được dịch liền mạch, đoạn trùng chữ (đầu bảng lặp ở trang sau) chỉ dịch một lần,
    và prompt hệ thống chỉ phải đọc một lần cho cả nhóm.
    Trang phải OCR thì dừng nhóm ở đó: OCR mất 30–70 giây/trang, gộp vào là người dùng chờ cả nhóm mới thấy gì.
    `force` (dịch lại một dải trang): trang đã dịch cũng được gộp vào. Trang có yêu cầu dịch riêng khác trang n thì
    dừng nhóm ở đó (xem instruction_text)."""
    limit = min(span, cfg.page_span or MAX_SPAN)
    if cfg.translator != "llm" or limit <= 1:
        return []
    budget, pages = await call_budget(cfg, system), []
    texts = {b["text"] for b in got[n][0]}                      # đoạn trùng chữ chỉ tính một lần (như khi dịch)
    size = sum(item_tokens(b) for b in got[n][0])
    for m in range(n + 1, min(n + limit, d["out"].page_count)):
        if (m in d["done"] and not force) or (nxt := await page_blocks(d, m, cfg, allow_ocr=False, fresh=True)) is None:
            break
        # một lượt gọi chỉ có MỘT system prompt: trang có yêu cầu dịch riêng (đã nhớ) khác trang đầu nhóm thì để
        # lượt sau, không thì yêu cầu của nó bị bỏ qua
        if d.get("instr", {}).get(m, "") != d.get("instr", {}).get(n, ""):
            break
        new = [b for b in nxt[0] if b["text"] not in texts]
        add = sum(item_tokens(b) for b in new)
        if size + add > budget or len(texts) + len(new) > MAX_ITEMS:
            break
        texts.update(b["text"] for b in new)
        size += add
        got[m] = nxt
        pages.append(m)
    return pages


async def ensure_glossary(d: dict, cfg: Config, src: pymupdf.Document) -> None:
    """Bảng thuật ngữ: một lượt gọi cho cả tài liệu, mọi trang dùng lại. Giao diện gọi trước để người dùng xem lại;
    dịch thẳng mà chưa có bảng thì lập luôn ở đây (trang nào xong trước thì lập)."""
    if "gloss" in d:
        return
    async with d.setdefault("glock", asyncio.Lock()):
        if "gloss" not in d:
            d["title"] = await asyncio.to_thread(doc_title, src)
            if (made := await build_glossary(cfg, src)) is None:
                return               # LLM lỗi: không ghi nhớ bảng rỗng, trang sau (hoặc nút "Lập lại") thử lại
            d["gloss"], d["gloss_extra"] = made["rows"], made["extra"]
            apply_gloss(d)
            store.save_state(d)
            print(f"[glossary] {len(d['glossary'])} thuật ngữ: "
                  + ", ".join(f"{a} → {b}" for a, b in list(d["glossary"].items())[:6]))


def block_hash(b: dict) -> str:
    """Dấu của một đoạn: đoạn người dùng đánh dấu "không dịch" chỉ còn hiệu lực khi đoạn cùng số hiệu vẫn là đoạn đó
    (trích lại khối với cấu hình OCR/layout khác thì số hiệu có thể trỏ sang đoạn khác)."""
    return hashlib.sha1(b["text"].encode()).hexdigest()[:10]


def refs_boxes(d: dict) -> dict[int, list[pymupdf.Rect]]:
    """Khung các khối chữ thuộc phần Tài liệu tham khảo (từ tiêu đề References tới Phụ lục hoặc hết bài), theo thứ tự
    đọc của MuPDF — nên đúng cả khi danh mục chảy qua hai cột. Tiêu đề "References" đứng riêng một khối thì vẫn dịch."""
    if "refs" not in d:
        out, scan = collections.defaultdict(list), RefsScan()
        for pno, page in enumerate(pymupdf.open(d["src"])):
            for b in page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]:
                lines = [" ".join("".join(sp["text"] for sp in ln["spans"]).split()) for ln in b.get("lines", [])]
                first = next((x for x in lines if x), "")
                if not first:
                    continue
                if (k := scan.step(first, block_style(b))) == "head" and len([x for x in lines if x]) == 1:
                    continue
                if k:
                    out[pno].append(pymupdf.Rect(b["bbox"]))
        d["refs"] = dict(out)
    return d["refs"]


def user_skips(d: dict, n: int, blocks: list[dict], heal: bool = True) -> set[int]:
    """Đoạn của trang n người dùng đánh dấu "không dịch" (⛔). Trích lại khối (đổi OCR/layout) thì số hiệu đoạn có thể
    đổi: tìm lại đoạn theo dấu chữ và ghi số hiệu mới; đoạn không còn (chữ trích khác đi) thì bỏ dấu. `heal=False`:
    `blocks` không phải khối dùng để dịch (lớp chữ tạm của trang chưa dịch) → chỉ tìm, không sửa dấu đã lưu."""
    marks = d.get("skip", {}).get(n)
    if not marks or not blocks:
        return set()
    if any(i >= len(blocks) or block_hash(blocks[i]) != h for i, h in marks.items()):
        where = {block_hash(b): i for i, b in enumerate(blocks)}
        fixed = {where[h]: h for h in marks.values() if h in where}
        if not heal:
            return set(fixed)
        if len(fixed) < len(marks):
            print(f"[skip] trang {n + 1}: {len(marks) - len(fixed)} đoạn ⛔ không còn sau khi trích lại → bỏ dấu")
        marks.clear()
        marks.update(fixed)
        if not marks:
            d["skip"].pop(n, None)
        store.save_state(d)
    return set(marks)


def skipped(d: dict, n: int, blocks: list[dict], cfg: Config) -> set[int]:
    """Đoạn của trang n KHÔNG dịch (giữ nguyên chữ gốc): người dùng đánh dấu (⛔), và phần Tài liệu tham khảo."""
    out = user_skips(d, n, blocks)
    if cfg.skip_refs and (boxes := refs_boxes(d).get(n)):
        for i, b in enumerate(blocks):
            c = pymupdf.Rect(b["bbox"])
            if any(r.contains((c.tl + c.br) / 2) for r in boxes):
                out.add(i)
    return out


def instruction_text(cfg: Config, d: dict, n: int) -> str:
    """Yêu cầu thêm của người đọc (cả bài, trang n, lượt này) — đặt SAU phần prompt giống nhau ở mọi lượt gọi, để
    server vẫn tái dùng được KV cache của phần đầu."""
    ins = d.get("instr", {})
    parts = list(dict.fromkeys(x.strip() for x in (ins.get("*", ""), ins.get(n, ""), cfg.instruction) if x and x.strip()))
    if not parts:
        return ""
    return ("Extra instructions from the reader for this translation. Follow them, unless they conflict with the rules "
            "above — the JSON format, max_len, placeholders and <b>/<i> tags are still mandatory:\n"
            + "\n".join(f"- {x[:500]}" for x in parts))


def rerender_page(d: dict, m: int, blocks: list[dict], cfg: Config, src: pymupdf.Document) -> None:
    """Dựng lại trang m của bản dịch từ bản dịch đã lưu (không gọi LLM): khôi phục trang gốc rồi đặt lại các đoạn đã
    dịch, trừ đoạn đang "không dịch". Gọi khi đang giữ d["lock"]."""
    if m in d["done"]:
        d["out"].delete_page(m)
        d["out"].insert_pdf(src, from_page=m, to_page=m, start_at=m)
    skip = skipped(d, m, blocks, cfg)
    tr = {i: t for i, t in d["trans"].get(m, {}).items() if i not in skip}
    d["placed"][m] = render_translation(d["out"][m], blocks, tr, cfg, src)
    d["done"].add(m)


@app.post("/api/{doc_id}/translate/{n}")
async def translate_page(doc_id: str, n: int, cfg: Config, force: bool = False, span: int = 1, blocks: str = ""):
    """Dịch trang n, và gộp thêm tối đa `span - 1` trang liền sau vào cùng một lượt gọi LLM nếu ngữ cảnh còn chỗ
    (`span` = số trang giao diện đã giữ chỗ cho lượt này). Trả về kết quả của từng trang đã dịch.
    `blocks` ("1,3,5"): chỉ dịch lại các đoạn đó của trang n, các đoạn khác giữ bản dịch cũ.
    Đoạn "không dịch" (⛔, Tài liệu tham khảo) không gửi LLM, chữ gốc giữ nguyên trên bản dịch."""
    d, cfg = get_doc(doc_id), resolve_keys(cfg)
    only = {int(x) for x in blocks.split(",") if x.strip().isdigit()}
    if n in d["done"] and not force and not only:
        return {"pages": [{"page": n, "cached": True}]}
    d.setdefault("keep_pages", set()).discard(n)       # trang đang "giữ bản gốc" mà dịch lại → thôi giữ
    got: dict[int, tuple] = {n: await page_blocks(d, n, cfg, fresh=not only)}
    src = pymupdf.open(d["src"])
    if "num_ctx" not in d:  # một giá trị cho cả tài liệu: đổi num_ctx giữa các trang là Ollama nạp lại model
        kind, top, _ = await server_info(cfg.base_url, cfg.model)
        d["num_ctx"] = min(doc_num_ctx(src, cfg), top) if kind == "ollama" else 0
    run = {"num_ctx": cfg.num_ctx or d["num_ctx"] or 8192, "tokens": collections.Counter()}
    token = _run.set(run)
    if cfg.instruction_scope in ("page", "doc"):       # nhớ yêu cầu (rỗng = bỏ yêu cầu đã nhớ)
        ins, key = d.setdefault("instr", {}), "*" if cfg.instruction_scope == "doc" else n
        if cfg.instruction.strip():
            ins[key] = cfg.instruction.strip()[:500]
        else:
            ins.pop(key, None)
        cfg = cfg.model_copy(update={"instruction": ""})
    try:
        if cfg.translator == "llm" and cfg.glossary_auto:
            await ensure_glossary(d, cfg, src)
        cfg = cfg.model_copy(update={"glossary": glossary_text(cfg, d)})  # để model chuyên dịch cũng theo bảng
        system = system_prompt(cfg, d)
        pages = [n] if only else [n] + await grow_group(d, n, cfg, span, system, got, force)
        if extra := instruction_text(cfg, d, n):
            system += "\n" + extra
        # (trang, số hiệu đoạn) của từng mục gửi đi: bỏ đoạn "không dịch", dịch lại một phần thì chỉ các đoạn đã chọn
        skip = {m: skipped(d, m, got[m][0], cfg) for m in pages}
        where = [(m, i) for m in pages for i in range(len(got[m][0])) if i not in skip[m] and (not only or i in only)]
        flat = [got[m][0][i] for m, i in where]
        context = await block_context(d, pages, cfg, flat) if cfg.translator == "llm" and flat else {}
        translations, stats = await translate_blocks(cfg, flat, context, system) if flat else \
            ({}, {"llm": 0, "google": 0, "errors": []})
    finally:
        _run.reset(token)
    out = []
    for m in pages:
        blocks_m, ocr = got[m]
        new = {i: translations[k] for k, (p, i) in enumerate(where) if p == m and k in translations}
        async with d["lock"]:
            d["trans"][m] = {**d["trans"].get(m, {}), **new} if only else new
            rerender_page(d, m, blocks_m, cfg, src)
        out.append({"page": m, "ocr": ocr, "blocks": len(blocks_m), "translated": len(d["trans"][m]),
                    "skipped": len(skipped(d, m, blocks_m, cfg))})
    async with d["lock"]:     # ghi bản dịch xuống đĩa: một lần cho cả nhóm trang (xem store.py)
        await asyncio.to_thread(store.save_pdf, d)
    store.save_state(d)
    return {"pages": out, "group": len(pages), "via_llm": stats["llm"], "via_google": stats["google"],
            "errors": stats["errors"][:1], "tokens": {**run["tokens"], "num_ctx": run["num_ctx"]}}


class KeepAsk(BaseModel):
    keep: bool = True


@app.post("/api/{doc_id}/page/{n}/keep")
async def keep_page(doc_id: str, n: int, ask: KeepAsk):
    """Giữ nguyên trang n như bản gốc (không dịch, không gọi LLM): trang bản dịch = trang gốc. keep=False: thôi giữ,
    trang về "chưa dịch"."""
    d = get_doc(doc_id)
    if not 0 <= n < d["out"].page_count:
        raise HTTPException(404)
    src = pymupdf.open(d["src"])
    keep = d.setdefault("keep_pages", set())
    async with d["lock"]:
        d["out"].delete_page(n)
        d["out"].insert_pdf(src, from_page=n, to_page=n, start_at=n)
        d["trans"].pop(n, None)
        d["placed"].pop(n, None)
        if ask.keep:
            keep.add(n)
            d["done"].add(n)
        else:
            keep.discard(n)
            d["done"].discard(n)
        await asyncio.to_thread(store.save_pdf, d)
    store.save_state(d)
    return {"page": n, "keep": ask.keep}


class SkipAsk(BaseModel):
    cfg: Config
    blocks: list[int]
    skip: bool = True


@app.post("/api/{doc_id}/page/{n}/skip")
async def skip_blocks(doc_id: str, n: int, ask: SkipAsk):
    """Đánh dấu / bỏ đánh dấu "không dịch" cho các đoạn của trang n. Trang đã dịch thì dựng lại ngay từ bản dịch đã
    lưu (không gọi LLM); bỏ đánh dấu một đoạn chưa từng được dịch thì dịch riêng các đoạn đó."""
    d, cfg = get_doc(doc_id), resolve_keys(ask.cfg)
    blocks, _ = await page_blocks(d, n, cfg)
    user_skips(d, n, blocks)
    marks = d.setdefault("skip", {}).setdefault(n, {})
    for i in ask.blocks:
        if 0 <= i < len(blocks):
            if ask.skip:
                marks[i] = block_hash(blocks[i])
            else:
                marks.pop(i, None)
    if not marks:
        d["skip"].pop(n, None)
    missing = [i for i in ask.blocks if not ask.skip and 0 <= i < len(blocks) and i not in d["trans"].get(n, {})
               and i not in skipped(d, n, blocks, cfg)]
    rendered = False
    if n in d["done"] and n not in d.get("keep_pages", ()):
        if missing:                      # đoạn chưa từng dịch → dịch riêng (translate_page tự dựng lại trang)
            await translate_page(doc_id, n, cfg.model_copy(update={"instruction": "", "instruction_scope": "once"}),
                                 blocks=",".join(map(str, missing)))
        else:
            async with d["lock"]:
                rerender_page(d, n, blocks, cfg, pymupdf.open(d["src"]))
                await asyncio.to_thread(store.save_pdf, d)
        rendered = True
    store.save_state(d)
    return {"page": n, "skip": sorted(skipped(d, n, blocks, cfg)), "user": sorted(d.get("skip", {}).get(n, {})),
            "rendered": rendered}


@app.post("/api/{doc_id}/glossary")
async def make_glossary(doc_id: str, cfg: Config, force: bool = False):
    """Lập (hoặc trả lại) bảng thuật ngữ của tài liệu để người dùng xem và chỉnh trên giao diện."""
    d, cfg = get_doc(doc_id), resolve_keys(cfg)
    if force:
        d.pop("gloss", None)
    await ensure_glossary(d, cfg, pymupdf.open(d["src"]))
    if "gloss" not in d:
        raise HTTPException(502, "Không gọi được LLM để lập bảng thuật ngữ")
    return {"title": d.get("title", ""), "terms": d["gloss"], "extra": d.get("gloss_extra", [])}


class GlossTerm(BaseModel):
    term: str
    vi: str = ""
    keep: bool = False   # giữ nguyên tiếng Anh
    use: bool = True     # bỏ tích = không đưa thuật ngữ này vào prompt


class GlossaryEdit(BaseModel):
    terms: list[GlossTerm]


@app.put("/api/{doc_id}/glossary")
def set_glossary(doc_id: str, body: GlossaryEdit):
    """Bảng người dùng đã chỉnh; các trang dịch sau đó dùng đúng bảng này (trang đã dịch phải bấm "Dịch lại")."""
    d = get_doc(doc_id)
    new = [t.model_dump() for t in body.terms]
    learn_terms(d.get("gloss", []), new)
    d["gloss"] = new
    have = {_term_key(t["term"].split()) for t in new if t["term"].strip()}
    d["gloss_extra"] = [x for x in d.get("gloss_extra", []) if _term_key(x["term"].split()) not in have]
    apply_gloss(d)
    store.save_state(d)
    return {"terms": d["gloss"], "used": len(d["glossary"]), "extra": d["gloss_extra"]}


@app.delete("/api/user_terms/blocked")
def unblock_term(term: str):
    """Bỏ chặn một thuật ngữ người dùng từng xoá (nút ↺ ở "gợi ý khác")."""
    if user_terms()["blocked"].pop(_term_key(term.split()), None) is not None:
        save_user_terms()
    return {"ok": True}


# ================================================================ Lớp chữ, tô vàng, trò chuyện
def page_lines(page: pymupdf.Page) -> list[dict]:
    """Dòng chữ của một trang để dựng lớp chữ trong suốt đè lên ảnh trang: bôi đen chọn chữ được như PDF thật.
    Lấy thẳng từ lớp chữ PDF (bản gốc, hoặc bản dịch đã chèn chữ Việt) nên toạ độ khớp đúng ảnh.
    `_x` (toạ độ trái của từng ký tự) chỉ dùng nội bộ cho reading_order, bị bỏ trước khi trả về."""
    out = []
    for b in page.get_text("rawdict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]:
        for ln in b.get("lines", ()):
            if abs(ln["dir"][1]) > 0.1:  # chữ xoay dọc (mã arXiv ở lề): không dựng lớp chữ
                continue
            chars = [c for sp in ln["spans"] for c in sp.get("chars", ())]
            text = "".join(c["c"] for c in chars)
            if not text.strip():
                continue
            out.append({"bbox": [round(v, 1) for v in ln["bbox"]], "text": text,
                        "size": round(max(sp["size"] for sp in ln["spans"]), 1),
                        "_x": [c["bbox"][0] for c in chars]})
    return out


def split_at(host: dict, xs: list[float]) -> list[dict]:
    """Cắt dòng `host` tại các hoành độ `xs` (chỗ có công thức chen vào giữa dòng)."""
    cuts = sorted({k for x in xs if 0 < (k := sum(cx < x for cx in host["_x"])) < len(host["_x"])})
    parts, prev = [], 0
    for k in [*cuts, len(host["_x"])]:
        if k <= prev:
            continue
        x0, x1 = host["_x"][prev], host["bbox"][2] if k == len(host["_x"]) else host["_x"][k]
        # "j": giao diện nối thẳng mẩu này vào mẩu trước, KHÔNG chèn thêm dấu cách — dấu cách quanh công thức
        # đã nằm sẵn trong chữ gốc ("… vị trí " + công thức + " có thể …"), chèn thêm là thừa.
        parts.append({"bbox": [round(x0, 1), host["bbox"][1], round(x1, 1), host["bbox"][3]],
                      "text": host["text"][prev:k], "size": host["size"], "b": host.get("b"),
                      "_x": host["_x"][prev:k], **({"j": 1} if prev else {})})
        prev = k
    return parts or [host]


def line_owner(bbox: list[float], rects: dict[int, tuple]) -> int | None:
    """Khối chứa dòng: khối nhỏ nhất mà tâm dòng nằm trong (khung các khối có thể chồng nhẹ lên nhau)."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    best = (None, float("inf"))
    for i, r in rects.items():
        if r[0] - 1 <= cx <= r[2] + 1 and r[1] - 1 <= cy <= r[3] + 1:
            if (area := (r[2] - r[0]) * (r[3] - r[1])) < best[1]:
                best = (i, area)
    return best[0]


def reading_order(lines: list[dict]) -> list[dict]:
    """Trả về danh sách dòng đã xếp theo thứ tự đọc TRONG TỪNG ĐOẠN (trên xuống, trái sang), và cắt dòng chữ ra
    nếu có công thức chen vào giữa. Trang dịch đổ chữ Việt trước rồi mới dán công thức (dạng vector) đè lên, nên
    theo thứ tự nội dung PDF thì chữ của công thức rơi xuống CUỐI đoạn: lớp chữ đọc ra "… nhỏ hơn . i" thay vì
    "… nhỏ hơn i.". Bôi đen, chép chữ và khớp câu sang bản kia đều sai theo.
    Chỉ xếp trong phạm vi từng đoạn, và giữ nguyên chỗ của đoạn trong thứ tự chung, nên bài hai cột không bị trộn."""
    slots: dict[int, list[dict]] = {}
    for ln in lines:
        if ln["b"] is not None:
            slots.setdefault(ln["b"], []).append(ln)

    def ordered(group: list[dict]) -> list[dict]:
        rows: list[list] = []   # [các dòng, y0, y1]
        for ln in sorted(group, key=lambda l: l["bbox"][1]):
            y0, y1 = ln["bbox"][1], ln["bbox"][3]
            # cùng hàng = chồng lấn dọc quá nửa chiều cao dòng thấp hơn (chỉ số trên/dưới của công thức tụt lên
            # xuống so với chữ thường, còn hai dòng chữ liền nhau chỉ chạm nhau ở phần đuôi chữ)
            if rows and min(y1, rows[-1][2]) - max(y0, rows[-1][1]) > 0.5 * min(y1 - y0, rows[-1][2] - rows[-1][1]):
                rows[-1][0].append(ln)
                rows[-1][1], rows[-1][2] = min(rows[-1][1], y0), max(rows[-1][2], y1)
            else:
                rows.append([[ln], y0, y1])
        out = []
        for row, _, _ in rows:
            pieces = []
            for ln in row:
                # công thức nằm LỌT giữa khoảng ngang của một dòng khác ("… nhỏ hơn |i| .") → cắt dòng đó ra
                inner = [o["bbox"][0] for o in row if o is not ln and ln["bbox"][0] < o["bbox"][0] < ln["bbox"][2]]
                pieces += split_at(ln, inner) if inner else [ln]
            out += sorted(pieces, key=lambda l: l["bbox"][0])
        return out

    done, out = set(), []
    for ln in lines:
        b = ln["b"]
        if b is None:
            out.append(ln)
        elif b not in done:      # đoạn xuất hiện lần đầu → đổ cả đoạn đã xếp vào đúng chỗ đó
            done.add(b)
            out += ordered(slots[b])
    for ln in out:
        ln.pop("_x", None)
    return out


@app.get("/api/{doc_id}/page/{n}/layer")
async def page_layer(doc_id: str, n: int, kind: str = "original", refs: bool = True):
    """Lớp chữ + khung từng đoạn của một trang. Số hiệu đoạn GIỐNG NHAU ở bản gốc và bản dịch, nên tô vàng một
    đoạn bên này là bên kia sáng đúng đoạn tương ứng."""
    d = get_doc(doc_id)
    if not 0 <= n < d["out"].page_count:
        raise HTTPException(404)
    if kind == "translated":
        if n not in d["done"]:
            return {"lines": [], "blocks": {}, "ready": False}
        page = d["out"][n]
        # đoạn không dịch (⛔, tài liệu tham khảo, trang giữ bản gốc) vẫn nằm đúng chỗ cũ trên bản dịch
        base = (d["blocks"].get(n) or (None, d.get(("layer", n)) or []))[1]
        rects = {i: tuple(b["bbox"]) for i, b in enumerate(base)}
        rects.update({i: v["rect"] for i, v in d["placed"].get(n, {}).items()})
    else:
        page = pymupdf.open(d["src"])[n]
        cached = d["blocks"].get(n)
        if cached is None:  # trang chưa dịch: gom đoạn từ lớp chữ (không OCR — trang scan thì chưa có lớp chữ)
            key = ("layer", n)
            if key not in d:
                d[key] = await asyncio.to_thread(extract_blocks, page)
            blocks = d[key]
        else:
            blocks = cached[1]
        rects = {i: tuple(b["bbox"]) for i, b in enumerate(blocks)}
    lines = await asyncio.to_thread(page_lines, page)
    for ln in lines:
        ln["b"] = line_owner(ln["bbox"], rects)
    lines = reading_order(lines)
    # đoạn không dịch: người dùng đánh dấu (⛔) và phần tài liệu tham khảo (nếu đang bật `refs`)
    src_blocks = (d["blocks"].get(n) or (None, d.get(("layer", n)) or []))[1]
    user = sorted(user_skips(d, n, src_blocks, heal=bool(d["blocks"].get(n))))
    auto = sorted(skipped(d, n, src_blocks, Config(skip_refs=True)) - set(user)) if refs else []
    return {"w": round(page.rect.width, 1), "h": round(page.rect.height, 1), "ready": True,
            "lines": lines, "blocks": {str(i): [round(v, 1) for v in r] for i, r in rects.items()},
            "skip": user, "refs": auto, "keep": n in d.get("keep_pages", ())}


@app.get("/api/{doc_id}/state")
def doc_state(doc_id: str):
    """Trạng thái tài liệu: tải lại trang, hay khởi động lại app, là lấy lại được bản dịch, tô vàng, bảng thuật ngữ
    (lưu trên đĩa, xem store.py — không phải dịch lại từ đầu)."""
    d = get_doc(doc_id)
    return {"id": doc_id, "name": d["name"], "pages": d["out"].page_count, "done": sorted(d["done"]),
            "marks": {str(p): v for p, v in d["marks"].items() if v},
            "terms": d.get("gloss", []), "extra": d.get("gloss_extra", []), "title": d.get("title", ""),
            "notes": N.ordered(d.get("notes", [])),
            "skip": {str(p): sorted(v) for p, v in d.get("skip", {}).items() if v},
            "keep": sorted(d.get("keep_pages", ())),
            "instr": {str(k): v for k, v in d.get("instr", {}).items()}}


# ---------------- Ghi chú (xem notes.py)
@app.get("/api/{doc_id}/notes")
def get_notes(doc_id: str):
    return {"notes": N.ordered(get_doc(doc_id).get("notes", []))}


@app.post("/api/{doc_id}/notes")
def save_note(doc_id: str, note: N.Note):
    """Thêm (id rỗng, hoặc "general" lần đầu) hoặc sửa chữ của một ghi chú. Trả về ghi chú đã lưu."""
    d = get_doc(doc_id)
    if (saved := N.upsert(d.setdefault("notes", []), note, d["out"].page_count)) is None:
        raise HTTPException(404, "Ghi chú này đã bị xoá")
    store.save_state(d)
    return saved


@app.delete("/api/{doc_id}/notes/{note_id}")
def delete_note(doc_id: str, note_id: str):
    d = get_doc(doc_id)
    notes = d.setdefault("notes", [])
    before = len(notes)
    notes[:] = [x for x in notes if x["id"] != note_id]   # sửa tại chỗ: đừng bỏ rơi ghi chú đang được thêm song song
    if len(notes) == before:
        raise HTTPException(404, "Không có ghi chú này")
    store.save_state(d)
    return {"deleted": note_id}


@app.get("/api/{doc_id}/notes.md")
def notes_md(doc_id: str):
    d = get_doc(doc_id)
    name = Path(d["name"] or "paper.pdf").stem + "_ghi-chu.md"
    return Response(N.markdown(d.get("title", ""), d["name"], d.get("notes", [])), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


class Mark(BaseModel):
    """Một vệt tô vàng: đúng phần chữ người dùng bôi đen, không phải cả đoạn.
    `s`/`e` là vị trí ký tự trong chữ của đoạn `block` ở bản `side`; `rects`/`peer` là các khung chữ (đơn vị PDF)
    để vẽ lại trên bản đó và bản kia — giao diện tính sẵn nên server chỉ việc cất và ghi vào PDF tải về."""
    id: str
    block: int
    side: str = "original"          # bôi đen ở bản nào
    s: int = 0
    e: int = 0
    text: str = ""
    rects: list[list[float]] = []   # khung ở bản `side`
    peer: list[list[float]] = []    # khung câu tương ứng ở bản kia
    ps: int = 0                     # vị trí ký tự của câu tương ứng đó (để biết vệt nào đang bị bôi đè)
    pe: int = 0
    via: str = ""                   # "llm": `peer` là đúng cụm tương ứng do LLM tìm; rỗng: cả câu tương ứng


class MarkEdit(BaseModel):
    page: int
    add: list[Mark] = []
    remove: list[str] = []          # id các vệt cần bỏ


@app.get("/api/{doc_id}/marks")
def get_marks(doc_id: str):
    return {"marks": {str(p): v for p, v in get_doc(doc_id)["marks"].items() if v}}


@app.post("/api/{doc_id}/marks")
def set_marks(doc_id: str, edit: MarkEdit):
    """Thêm / bỏ vệt tô vàng. Lưu theo tài liệu ở server: tải lại trang vẫn còn, và PDF tải về cũng có."""
    d = get_doc(doc_id)
    marks = d["marks"].setdefault(edit.page, [])
    if edit.remove:
        marks[:] = [m for m in marks if m["id"] not in set(edit.remove)]
    marks.extend(m.model_dump() for m in edit.add)
    store.save_state(d)
    return {"page": edit.page, "marks": marks}


# ---------------- Căn vệt tô bằng LLM
# Bôi đen vài chữ ở một bản → hỏi LLM cụm nào ở bản kia nói đúng điều đó, để tô đúng cụm thay vì cả câu.
# LLM chép lại NGUYÊN VĂN cụm đó; server tìm lại cụm trong câu bên kia để ra vị trí ký tự. Không tìm thấy (model
# sửa chữ, bịa, trả JSON rác) → giao diện giữ vệt tô cả câu như khi không có LLM, không bao giờ tô bậy.
ALIGN_PROMPT = """You align a highlighted phrase between a research-paper passage and its translation (English/Chinese <-> Vietnamese).
Input JSON: {"source": "...", "target": "..."}. In "source", the highlighted part is wrapped in [[ and ]]. "target" is the corresponding text in the other language.
Return JSON {"match": "..."}: the part of "target" that translates EXACTLY the highlighted words — no more, no less.
- Copy it character for character from "target" (same spelling, accents, spaces and punctuation). Never translate, paraphrase or fix it.
- Do NOT extend it to words that translate the non-highlighted words around it, even if that makes a nicer phrase. A highlighted single word usually matches one or two words.
- If the highlighted part cuts a word in half, match the whole translated word. If nothing in "target" corresponds, return "".
Examples:
{"source": "The [[encoder]] maps an input sequence to continuous representations.", "target": "Bộ mã hoá ánh xạ chuỗi đầu vào thành các biểu diễn liên tục."} -> {"match": "Bộ mã hoá"}
{"source": "These models [[were trained on]] eight P100 GPUs for 12 hours.", "target": "Các mô hình này được huấn luyện trên tám GPU P100 trong 12 giờ."} -> {"match": "được huấn luyện trên"}
{"source": "Chúng tôi [[đề xuất]] một kiến trúc mạng mới, Transformer.", "target": "We propose a new network architecture, the Transformer."} -> {"match": "propose"}"""
ALIGN_SCHEMA = {"type": "object", "properties": {"match": {"type": "string"}}, "required": ["match"]}
_ALIGN_CACHE: dict[tuple, dict] = {}
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "\u00a0": " "})


def align_cfg(cfg: Config) -> Config:
    """Server/model căn vệt tô: để trống thì dùng đúng server đang dịch. temperature 0: cần chép đúng, không cần hay."""
    return side_cfg(cfg, cfg.align_base_url, cfg.align_api_key, cfg.align_model).model_copy(update={"temperature": 0.0})


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Chữ đã chuẩn hoá để so (NFC, chữ thường, ngoặc kép/gạch nối kiểu in ấn → ASCII, gộp khoảng trắng) kèm vị trí
    ký tự gốc của từng ký tự, để tìm được rồi thì trả về đúng vị trí trong chữ gốc."""
    out, pos = [], []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFC", ch).translate(_QUOTES):
            c = c.lower() if len(c.lower()) == 1 else c
            if c.isspace():
                if not out or out[-1] == " ":
                    continue
                c = " "
            out.append(c)
            pos.append(i)
    return "".join(out), pos


def locate(match: str, target: str, hint: float) -> tuple[int, int] | None:
    """Vị trí [đầu, cuối) của `match` trong `target`: đúng từng chữ trước, không có thì so sau khi chuẩn hoá, rồi bỏ
    dấu câu thừa ở hai đầu. Xuất hiện nhiều lần ("attention" hai lần trong câu) → lấy chỗ gần `hint` nhất
    (vị trí tương đối 0–1 của phần bôi đen trong câu bên này)."""
    def best(hay: str, needle: str) -> int | None:
        hits, at = [], hay.find(needle)
        while at >= 0 and needle:
            hits.append(at)
            at = hay.find(needle, at + 1)
        if not hits:
            return None
        return min(hits, key=lambda a: abs((a + len(needle) / 2) / max(len(hay), 1) - hint))
    match = match.strip()
    if not match:
        return None
    if (a := best(target, match)) is not None:
        return a, a + len(match)
    hay, pos = _norm_map(target)
    for needle in (match, match.strip(" .,;:!?\"'()[]")):
        needle = _norm_map(needle)[0].strip()
        if len(needle) >= 1 and (a := best(hay, needle)) is not None:
            return pos[a], pos[a + len(needle) - 1] + 1
    return None


class AlignAsk(BaseModel):
    cfg: Config
    src: str                        # câu (hoặc các câu) chứa phần bôi đen
    s: int                          # phần bôi đen: [s, e) trong `src`
    e: int
    dst: str                        # câu tương ứng ở bản kia (lệch số câu thì cả đoạn)


@app.post("/api/{doc_id}/align")
async def align(doc_id: str, ask: AlignAsk):
    """Cụm ở `dst` tương ứng với phần bôi đen trong `src`: {"s", "e"} (vị trí ký tự trong `dst`) hoặc
    {"s": null} kèm lý do — khi đó giao diện giữ vệt tô cả câu."""
    if doc_id not in DOCS and not store.exists(doc_id):   # chỉ kiểm tài liệu có thật, khỏi nạp cả PDF từ đĩa
        raise HTTPException(404, "Không tìm thấy tài liệu")
    cfg = align_cfg(resolve_keys(ask.cfg))
    s, e = max(0, ask.s), min(len(ask.src), ask.e)
    if not ask.dst.strip() or e <= s:
        return {"s": None, "reason": "không có chữ để căn"}
    key = (cfg.base_url, cfg.model, ask.src, s, e, ask.dst)
    if key in _ALIGN_CACHE:
        return {**_ALIGN_CACHE[key], "cached": True}
    marked = f"{ask.src[:s]}[[{ask.src[s:e]}]]{ask.src[e:]}"
    t0 = time.perf_counter()
    hint = ((s + e) / 2) / max(len(ask.src), 1)
    match, hit = "", None
    # Trả rỗng / trả cụm không có thật / trả chữ không phải JSON: hỏi lại đúng câu đó một lần. Đo 24/09/2026:
    # gpt-oss thỉnh thoảng trả {"match": ""} ở lần hỏi đầu rồi hỏi lại thì đúng ("chi tiết" → "details").
    for _ in range(2):
        try:
            # đầu ra chỉ là chữ chép lại từ `dst`: trần token theo độ dài `dst` (tiếng Việt ~2,5 ký tự/token)
            data = await chat_json(cfg, ALIGN_PROMPT, {"source": marked, "target": ask.dst}, ALIGN_SCHEMA,
                                   max_out=len(ask.dst) // 2 + 3 * (e - s) + 96)
        except LLMError as err:
            return {"s": None, "reason": f"LLM lỗi: {err}"[:300]}
        except ValueError:                  # model trả chữ không phải JSON
            continue
        match = str(data.get("match") or "") if isinstance(data, dict) else ""
        if hit := locate(match, ask.dst, hint):
            break
    res = {"s": hit[0], "e": hit[1], "match": ask.dst[hit[0]:hit[1]]} if hit else \
          {"s": None, "reason": f"LLM trả về cụm không có trong câu bên kia: {match[:120]!r}" if match else "LLM không tìm thấy cụm tương ứng"}
    res["ms"] = round((time.perf_counter() - t0) * 1000)
    if hit:                             # chỉ nhớ khi căn được: model trả rỗng thất thường, lần tô sau hỏi lại
        if len(_ALIGN_CACHE) > 2000:
            _ALIGN_CACHE.clear()
        _ALIGN_CACHE[key] = res
    return res


_HEADING = re.compile(r"\d+(?:\.\d+)*\.?\s+[A-Z][\w ,:()/&'\-–]{2,70}")
_NAMED = ("abstract", "introduction", "related work", "background", "method", "methodology", "experiments",
          "evaluation", "results", "discussion", "conclusion", "conclusions", "limitations", "references")


def doc_outline(doc: pymupdf.Document, limit: int = 60) -> list[str]:
    """Mục lục thô của bài ("tr.4 · 3.2 Scaled Dot-Product Attention") để trả lời được câu hỏi về cả tài liệu
    mà không phải nhét toàn văn vào prompt."""
    out = []
    for n, page in enumerate(doc):
        for ln in page.get_text().splitlines():
            t = " ".join(ln.split())
            if (_HEADING.fullmatch(t) or t.lower().rstrip(":") in _NAMED) and t not in {x.split("· ")[-1] for x in out}:
                out.append(f"tr.{n + 1} · {t}")
                if len(out) >= limit:
                    return out
    return out


def formula_text(page: pymupdf.Page, f: dict) -> str:
    """Chữ của một công thức lấy thẳng từ glyph trong PDF (vùng cắt đã bó sát nét mực). Không đẹp như LaTeX — chỉ số
    trên/dưới nằm ngang hàng, căn thức mất dấu — nhưng LLM còn thấy công thức nói về cái gì, thay vì chỗ giữ {v1}."""
    r = pymupdf.Rect(f["clip"]) + (-0.5, -0.5, 0.5, 0.5)
    # clip lấy cả ký tự chỉ chạm mép vùng (chữ dòng trên chạm đỉnh dấu căn) → chỉ giữ đúng các glyph của công thức,
    # nhận ra theo toạ độ chân chữ đã ghi lúc trích (f["chars"])
    mine = f.get("chars") or []
    ours = lambda o: not mine or any(abs(o[0] - x) < 0.6 and abs(o[1] - y) < 0.6 for x, y in mine)  # noqa: E731
    out = []
    for blk in page.get_text("rawdict", clip=r)["blocks"]:
        for ln in blk.get("lines", ()):
            for sp in ln["spans"]:
                out += [c["c"] for c in sp["chars"] if ours(c["origin"])]
            out.append(" ")
    return " ".join("".join(out).split())


_EQ_NO = re.compile(r"\(\s*\d{1,3}[a-z]?\s*\)\s*$")


def display_formulas(page: pymupdf.Page, blocks: list[dict]) -> list[tuple[pymupdf.Rect, str]]:
    """Công thức đứng riêng dòng (layout bỏ qua khi trích đoạn để dịch, nên không nằm trong đoạn nào): các dòng chữ
    nằm ngoài mọi đoạn, gom theo khoảng cách dọc (tử số / mẫu số / số hiệu công thức là các dòng riêng), giữ nhóm
    có ≥ 30% ký tự in bằng font công thức, hoặc có "=" và số hiệu "(1)" ở cuối."""
    boxes = [pymupdf.Rect(b["bbox"]) + (-1, -1, 1, 1) for b in blocks]
    cand = []
    for blk in page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)["blocks"]:
        for ln in blk.get("lines", ()):
            if abs(ln["dir"][1]) > 0.1:                        # chữ xoay dọc bên lề (arXiv…)
                continue
            r = pymupdf.Rect(ln["bbox"])
            if any(bx.contains((r.tl + r.br) / 2) for bx in boxes):
                continue
            text = "".join(sp["text"] for sp in ln["spans"])
            chars = sum(len(sp["text"].strip()) for sp in ln["spans"])
            math = sum(len(sp["text"].strip()) for sp in ln["spans"] if MATH_FONT.search(sp["font"]))
            if text.strip():
                cand.append([r, text.strip(), chars, math])
    cand.sort(key=lambda c: (c[0].y0, c[0].x0))
    # gom: dòng cách nhóm theo chiều dọc < 1,6 dòng và chồng nhau theo chiều ngang (±60pt: số hiệu công thức ở lề
    # phải, dấu ngoặc lớn của ma trận). Gộp lặp tới khi không gộp thêm được (ma trận nhiều tầng tới từ nhiều phía).
    groups = [[pymupdf.Rect(c[0]), [(c[0].y0, c[0].x0, c[1])], c[2], c[3]] for c in cand]
    near = lambda a, b: (b.y0 - a.y1 < 1.6 * max(min(a.height, b.height), 6) and a.y0 - b.y1 < 1.6 * max(min(a.height, b.height), 6)  # noqa: E731
                         and a.x0 < b.x1 + 60 and a.x1 > b.x0 - 60)
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if near(groups[i][0], groups[j][0]):
                    gi, gj = groups[i], groups.pop(j)
                    gi[0] |= gj[0]
                    gi[1] += gj[1]
                    gi[2] += gj[2]
                    gi[3] += gj[3]
                    merged = True
                    break
            if merged:
                break
    groups = [[r, [t for *_, t in sorted(lines)], c, m] for r, lines, c, m in groups]
    out = []
    for r, lines, chars, math in groups:
        text = " ".join(lines)
        if chars < 3 or chars > 2000 or r.width < 30:
            continue
        if math >= 0.3 * chars or ("=" in text and _EQ_NO.search(text)):
            out.append((r, text if len(text) <= 700 else text[:700] + " …"))   # ma trận lớn: đủ biết là gì
    return out


# ---------------- Công thức → LaTeX (nhận dạng từ ảnh)
# Chữ chép từ glyph của PDF mất cấu trúc hai chiều: ma trận ra một dãy ô lẫn lộn, phân số/căn thức mất gạch (gạch là
# nét vẽ, không phải ký tự). Cắt ảnh từng công thức cho model đọc ảnh chép ra LaTeX thì đúng: đo 25/09/2026 trên ma
# trận xoay (15) của RoFormer, PaddleOCR-VL local ("Formula Recognition:"), dots-3-note-preview:free và
# nex-n2.5-pro:free đều chép đúng cả 7×7 ô. Làm một lần cho mỗi công thức, lưu trong state.json (d["flatex"]:
# trang → {dấu glyph → LaTeX}; "" = đã thử mà không dùng được → giữ glyph, không thử lại).
FORMULA_PROMPT = ("Transcribe the mathematical formula in this image into LaTeX. Output only the LaTeX code, without "
                  "$ delimiters, equation number or explanation.")
FORMULA_WAIT = 30        # giây chờ nhận dạng công thức của trang chính trước khi trả lời; còn lại chạy nền
FORMULA_SPREAD = 4.5     # công thức trong dòng có chân chữ lệch nhau > 4,5pt (phân số, tổng có cận, chỉ số hai tầng)
FORMULA_RETRY = 600      # server nhận dạng lỗi (chưa bật Ollama…) → 10 phút không thử lại


def formula_cfg(cfg: Config) -> Config | None:
    """Model chép công thức sang LaTeX (xem Config.formula_ocr); None = tắt / chưa cấu hình."""
    if cfg.formula_ocr == "see":
        return see_cfg(cfg)
    if cfg.formula_ocr == "ocr" and cfg.ocr_vlm_model:
        return side_cfg(cfg, cfg.ocr_vlm_base_url, cfg.ocr_vlm_api_key, cfg.ocr_vlm_model)
    return None


def formula_prompt(model: str) -> str:
    m = model.lower()
    if "paddleocr-vl" in m or "glm-ocr" in m:   # prompt chính thức của hai model OCR này cho vùng công thức
        return "Formula Recognition:"
    return FORMULA_PROMPT


def fkey(glyph: str, rect) -> str:
    """Dấu của một công thức: theo chữ glyph (trích lại khối, đổi số thứ tự đoạn vẫn khớp); công thức không có glyph
    (vẽ bằng nét) thì theo vị trí."""
    return hashlib.sha1((glyph or repr([round(v) for v in rect])).encode()).hexdigest()[:12]


def glyphs_in(page: pymupdf.Page, rect) -> str:
    """Chữ glyph của các ký tự có tâm nằm trong vùng (clip của MuPDF lấy cả ký tự chỉ chạm mép)."""
    r, out = pymupdf.Rect(rect), []
    for blk in page.get_text("rawdict", clip=r)["blocks"]:
        for ln in blk.get("lines", ()):
            if abs(ln["dir"][1]) > 0.1:
                continue
            for sp in ln["spans"]:
                out += [c["c"] for c in sp["chars"] if r.contains(((c["bbox"][0] + c["bbox"][2]) / 2,
                                                                   (c["bbox"][1] + c["bbox"][3]) / 2))]
            out.append(" ")
    return " ".join("".join(out).split())


def page_display(d: dict, n: int, page: pymupdf.Page | None = None) -> list[dict]:
    """Công thức đứng riêng dòng của trang n: [{"bbox", "no" (số hiệu "(15)" hoặc ""), "text" (glyph), "key"}].
    Có PP-DocLayout thì theo vùng display_formula của nó — gọn từng công thức, số hiệu là vùng riêng; không nạp được
    model thì gom dòng như display_formulas. Tính một lần cho mỗi trang (layout ~1 giây/trang)."""
    if ("fdisp", n) not in d:
        page = page or pymupdf.open(d["src"])[n]
        blocks = (d["blocks"].get(n) or (None, d.get(("layer", n)) or []))[1]
        regs = detect_layout(page) if page_has_text(page) else None
        out = []
        if regs is None:
            out = [{"bbox": tuple(r), "no": "", "text": t} for r, t in display_formulas(page, blocks)]
        else:
            nums = [(g["bbox"], glyphs_in(page, g["bbox"])) for g in regs if g["label"] == "formula_number"]
            for g in regs:
                if g["label"] != "display_formula":
                    continue
                x0, y0, x1, y1 = g["bbox"]
                no = next((t for b, t in nums if b[1] < y1 and b[3] > y0 and b[0] >= x1 - 5 and _EQ_NO.search(t)), "")
                text = glyphs_in(page, g["bbox"])
                if len(text) > 700:
                    text = text[:700] + " …"
                out.append({"bbox": tuple(round(v, 1) for v in g["bbox"]), "no": no.strip(), "text": text})
        for f in out:
            f["key"] = fkey(f["text"], f["bbox"])
        d["fdisp", n] = out
    return d["fdisp", n]


def page_layer_blocks(d: dict, n: int, page: pymupdf.Page | None = None) -> list[dict]:
    """Khối chữ của trang n: khối đã dịch, không thì lớp chữ (trích như khi mở trang: layout, không OCR)."""
    if (got := d["blocks"].get(n)) is not None:
        return got[1]
    if ("layer", n) not in d:
        page = page or pymupdf.open(d["src"])[n]
        d["layer", n] = extract_blocks(page) if page_has_text(page) else []
    return d["layer", n]


def formula_jobs(d: dict, n: int) -> list[tuple[str, tuple, str, dict | None]]:
    """Các công thức của trang n nên chép sang LaTeX: (dấu, vùng cắt, glyph, công thức trong dòng hoặc None) — mọi công
    thức đứng riêng dòng, và công thức trong dòng có nhiều tầng (glyph của chúng mới hay lộn xộn; x_i, W^Q chép glyph
    vẫn đọc được)."""
    page = pymupdf.open(d["src"])[n]
    jobs = [(f["key"], f["bbox"], f["text"], None) for f in page_display(d, n, page)]
    blocks = page_layer_blocks(d, n, page)
    for b in blocks:
        for f in b.get("formulas", ()):
            ys = [y for _, y in f.get("chars") or ()]
            if ys and max(ys) - min(ys) > FORMULA_SPREAD:
                g = formula_text(page, f)
                jobs.append((fkey(g, f["clip"]), tuple(f["clip"]), g, f))
    return list({j[0]: j for j in jobs}.values())


_GREEK = {"ell": "ℓ", "partial": "∂", "nabla": "∇", "infty": "∞", "sum": "∑", "prod": "∏", "int": "∫", "sqrt": "√",
          "in": "∈", "top": "⊤", "intercal": "⊺", "times": "×", "cdot": "·", "le": "≤", "leq": "≤", "ge": "≥",
          "geq": "≥", "neq": "≠", "approx": "≈", "odot": "⊙", "oplus": "⊕", "otimes": "⊗", "prime": "′"}


def latex_plain(latex: str) -> str:
    """LaTeX → chuỗi ký tự gần với glyph (để đối chiếu): \\theta → θ, \\cos → cos, bỏ lệnh định dạng."""
    def cmd(m: re.Match) -> str:
        name = m[1]
        if name in _GREEK:
            return _GREEK[name]
        for case in ("SMALL", "CAPITAL"):
            for nm in (name, name.removeprefix("var")):
                try:
                    ch = unicodedata.lookup(f"GREEK {case} LETTER {nm.upper()}")
                    if (case == "CAPITAL") == nm[:1].isupper():
                        return ch
                except KeyError:
                    pass
        if name in ("sin", "cos", "tan", "exp", "log", "ln", "max", "min", "arg", "softmax", "det", "lim", "sup", "inf"):
            return name
        return " "
    return re.sub(r"\\([A-Za-z]+)", cmd, latex)


def latex_matches(latex: str, glyph: str) -> bool:
    """LaTeX nhận dạng có khớp chữ glyph của công thức không: so bội tập chữ cái/số (bỏ qua ký hiệu, định dạng).
    Chặn trường hợp model đọc nhầm vùng hay bịa (chép cả chữ dòng bên cạnh, lặp vô hạn). Không có glyph thì tin model."""
    norm = lambda s: collections.Counter(c for c in unicodedata.normalize("NFKC", s) if c.isalnum())  # noqa: E731
    g, t = norm(glyph), norm(latex_plain(latex))
    if sum(g.values()) < 3:
        return bool(t)
    common = sum((g & t).values())
    return common >= 0.6 * sum(g.values()) and common >= 0.5 * sum(t.values())


def clean_latex(text: str) -> str:
    """Bỏ phần suy nghĩ, ``` và dấu bao công thức ($$, \\[ \\], \\( \\)) mà model hay thêm."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    t = re.sub(r"```[a-z]*", "", t).strip()
    t = re.sub(r"(\$|\\\]|\\\))\s*[.,;:]+$", r"\1", t)     # "\(x\)." — dấu câu của câu văn chép kèm
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if t.startswith(a) and t.endswith(b) and len(t) > len(a) + len(b):
            t = t[len(a):-len(b)].strip()
            break
    t = re.sub(r"^\\begin\{(equation|align|displaymath)\*?\}(.*)\\end\{\1\*?\}$", r"\2", t, flags=re.S).strip()
    t = re.sub(r"\\\)\s*\\\(", " ", t)     # PaddleOCR-VL nối các dòng của công thức nhiều dòng bằng "\) \("
    return re.sub(r"\s+", " ", t)


async def recognize_page(d: dict, n: int, fc: Config) -> int:
    """Chép sang LaTeX các công thức chưa có của trang n (xem formula_jobs); trả về số công thức đã thử."""
    lat = d.setdefault("flatex", {})
    jobs = [j for j in await asyncio.to_thread(formula_jobs, d, n) if j[0] not in lat.get(n, {})]
    if not jobs:
        return 0
    headers = {"Content-Type": "application/json"}
    if fc.api_key:
        headers["Authorization"] = f"Bearer {fc.api_key}"
    if "openrouter.ai" in fc.base_url:
        headers |= {"HTTP-Referer": "http://localhost", "X-Title": "PDF Paper Translator"}
    url = fc.base_url.rstrip("/") + "/chat/completions"
    src = pymupdf.open(d["src"])
    # công thức trong dòng: cắt từ bản sao trang chỉ còn ký tự của công thức — vùng cắt nằm giữa câu văn, để nguyên thì
    # model chép cả chữ bên cạnh ("… in generating the", đo 25/09/2026 trên DataSentinel) và bị loại khi đối chiếu
    inline = [j[3] for j in jobs if j[3]]
    fdoc = await asyncio.to_thread(formula_source, src, n, inline) if inline else None
    _, _, slots = await server_info(fc.base_url, fc.model)
    slots = fc.ocr_vlm_concurrency if fc.base_url == fc.ocr_vlm_base_url else slots

    async def one(client: httpx.AsyncClient, key: str, rect, glyph: str, f: dict | None):
        r = pymupdf.Rect(formula_reach(f) if f else rect) + (-2, -1.5, 2, 1.5)
        zoom = min(6.0, max(3.0, 48 / max(r.height, 1)))   # dòng công thức cao ≥ ~48px
        png = (fdoc if f else src)[0 if f else n].get_pixmap(clip=r, matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        body = {"model": fc.model, "temperature": 0, "max_tokens": 1500, "messages": [{"role": "user", "content": [
            {"type": "text", "text": formula_prompt(fc.model)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
        if re.search(r"ocr", fc.model, re.I):
            body["stop"] = ["```"]
        no_think(fc, url, body)
        async with llm_sem(fc.base_url, slots):
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise LLMError(f"Lỗi {resp.status_code}: {resp.text[:200]}")
        choice = resp.json()["choices"][0]
        latex = clean_latex((choice.get("message") or {}).get("content") or "")
        ok = choice.get("finish_reason") != "length" and latex and len(latex) < 4000 and latex_matches(latex, glyph)
        if not ok:
            print(f"[công thức] trang {n + 1}: bỏ kết quả nhận dạng {latex[:80]!r} (glyph {glyph[:60]!r})")
        lat.setdefault(n, {})[key] = latex if ok else ""

    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=180)) as client:
        res = await asyncio.gather(*(one(client, *j) for j in jobs), return_exceptions=True)
    errs = [e for e in res if isinstance(e, BaseException)]
    if errs:
        d["flatex_fail"] = time.monotonic()
        print(f"[công thức] trang {n + 1}: {len(errs)}/{len(jobs)} công thức lỗi ({fc.model} @ {fc.base_url}): "
              f"{errs[0]!r}"[:300])
    store.save_state(d)
    return len(jobs)


def formulas_ready(d: dict, pages: list[int], cfg: Config) -> list[asyncio.Task]:
    """Bắt đầu (nếu chưa chạy) việc chép công thức sang LaTeX cho các trang; trả về việc của từng trang để ai cần thì
    chờ. Server lỗi gần đây thì thôi (chữ glyph vẫn dùng được)."""
    fc = formula_cfg(cfg)
    if fc is None or time.monotonic() - d.get("flatex_fail", -FORMULA_RETRY) < FORMULA_RETRY:
        return []
    tasks, loop = d.setdefault("flatex_tasks", {}), asyncio.get_running_loop()
    out = []
    for n in dict.fromkeys(pages):
        t = tasks.get(n)
        if t is None or t.done() or t.get_loop() is not loop:
            t = tasks[n] = loop.create_task(recognize_page(d, n, fc))
            t.add_done_callback(lambda t: t.cancelled() or t.exception())   # lỗi đã in trong recognize_page
        out.append(t)
    return out


def page_pair(d: dict, n: int, mode: str = "pair") -> str:
    """Chữ của một trang để gửi LLM, ghép theo từng đoạn. `mode`: "pair" = chữ gốc kèm bản dịch tiếng Việt,
    "source" = chỉ chữ gốc (đỡ nửa số token), "translation" = chỉ bản dịch (đoạn chưa dịch thì chữ gốc).
    Công thức ({vN} trong chữ đã trích) được thay bằng chữ của công thức đặt trong ⟦…⟧.
    Trang chưa trích khối (chưa dịch, chưa mở lớp chữ) thì lấy thẳng chữ của trang — hỏi về trang nào cũng có nội
    dung để LLM đọc."""
    blocks = (d["blocks"].get(n) or (None, d.get(("layer", n)) or []))[1]
    if not blocks and d.get("flatex", {}).get(n):   # trang đã có LaTeX công thức: trích khối để thay đúng chỗ
        blocks = page_layer_blocks(d, n)
    if not blocks:
        return pymupdf.open(d["src"])[n].get_text().strip()
    tr = d["trans"].get(n, {})
    ftext = d.setdefault("ftext", {})         # (trang, đoạn) → [chữ từng công thức]: đọc glyph một lần thôi
    lat = d.get("flatex", {}).get(n, {})      # dấu công thức → LaTeX đã nhận dạng từ ảnh (recognize_page)
    page = None
    # công thức đứng riêng dòng → chèn ngay sau đoạn nằm trên nó (cùng cột), để LLM đọc đúng mạch "… as: <công thức>"
    after: dict[int, list[str]] = collections.defaultdict(list)
    for f in page_display(d, n):
        r = f["bbox"]
        above = [(b["bbox"][3], i) for i, b in enumerate(blocks)
                 if b["bbox"][3] <= r[1] + 2 and b["bbox"][0] < r[2] and b["bbox"][2] > r[0]]
        body = f"$${lat[f['key']]}$$" if lat.get(f["key"]) else f["text"]
        after[max(above)[1] if above else -1].append(f"[công thức{' ' + f['no'] if f['no'] else ''}] ⟦{body}⟧")
    parts = list(after.get(-1, []))
    for i, b in enumerate(blocks):
        src, vi = untag(b["text"]), untag(tr.get(i, ""))
        if b.get("formulas") and "{v" in src + vi:
            if (n, i) not in ftext:
                page = page or pymupdf.open(d["src"])[n]
                ftext[n, i] = [(formula_text(page, f), f["clip"]) for f in b["formulas"]]
            fs = [f"${lat[k]}$" if lat.get(k := fkey(g, r)) else g for g, r in ftext[n, i]]
            put = lambda m: f"⟦{fs[int(m[1]) - 1]}⟧" if 0 < int(m[1]) <= len(fs) else ""  # noqa: E731
            src, vi = PLACEHOLDER.sub(put, src), PLACEHOLDER.sub(put, vi)
        if mode == "source":
            parts.append(f"[{i}] {src}")
        elif mode == "translation":
            parts.append(f"[{i}] {vi or src}")
        else:
            parts.append(f"[{i}] {src}" + (f"\n    → {vi}" if vi and vi != src else ""))
        parts += after.get(i, [])
    return "\n".join(parts)


def side_cfg(cfg: Config, base_url: str, api_key: str, model: str) -> Config:
    """Cấu hình cho một việc phụ (trò chuyện, thuật ngữ, căn vệt tô) có thể chạy trên server riêng: để trống base URL
    thì dùng đúng server + key đang dịch. Có server riêng thì CHỈ dùng key của server đó (resolve_keys đã điền key
    đã nhớ theo base URL) — không bao giờ lấy key của server dịch gửi sang server khác."""
    if not base_url:
        return cfg.model_copy(update={"model": model or cfg.model})
    return cfg.model_copy(update={"base_url": base_url, "api_key": api_key, "model": model or cfg.model})


def gloss_cfg(cfg: Config) -> Config:
    """Server/model lập bảng thuật ngữ: để trống trong cấu hình thì dùng đúng server đang dịch."""
    return side_cfg(cfg, cfg.gloss_base_url, cfg.gloss_api_key, cfg.gloss_model)


def chat_cfg(cfg: Config) -> Config:
    """Server/model để trò chuyện: để trống trong cấu hình thì dùng đúng server đang dịch."""
    return side_cfg(cfg, cfg.chat_base_url, cfg.chat_api_key, cfg.chat_model)


def see_cfg(cfg: Config) -> Config | None:
    """Server/model xem ảnh trang: model trò chuyện nếu đã biết nó đọc được ảnh (danh sách model của OpenRouter
    cho biết), không thì model "xem ảnh trang" riêng; chưa cấu hình thì None (không gửi ảnh)."""
    c = chat_cfg(cfg)
    if (MODEL_META.get(c.base_url.rstrip("/"), {}).get(c.model) or {}).get("vision"):
        return c
    return side_cfg(cfg, cfg.see_base_url, cfg.see_api_key, cfg.see_model) if cfg.see_model else None


CONTEXT_MODES = ("auto", "pair", "source", "translation", "image", "full", "pdf", "near")  # near: chỉ để bench so sánh
CHAT_BUDGET = 12000   # token ngữ cảnh tối đa khi không hỏi về cả bài (≈ 5–8 trang gốc + dịch)
PDF_MAX_MB = 20       # "Nguyên PDF": file lớn hơn thì gửi chữ (OpenRouter/OpenAI nhận file base64 trong JSON)


def pdf_input(c: Config) -> str:
    """Server nhận thẳng file PDF trong tin nhắn (`{"type": "file"}`) không, và đọc bằng cách nào:
    - OpenRouter: plugin file-parser — "native" với model tự đọc được PDF (Claude, Gemini, GPT-4o trở lên: thấy cả
      hình, tính token như ảnh trang), còn lại "pdf-text" (trích chữ, miễn phí; đo 25/09/2026 với nex-n2.5-pro:free:
      BERT 18,8k token, 5 giây, đúng cả số liệu trong bảng). Không dùng "mistral-ocr" (tính tiền theo trang).
    - OpenAI (api.openai.com): model đọc thẳng file.
    - "" = không nhận: proxy ChatGPT trả 200 nhưng lặng lẽ bỏ file, llama.cpp trả 400, Ollama/LM Studio không có kiểu
      nội dung này."""
    url = c.base_url.lower()
    if "openrouter.ai" in url:
        return "native" if re.match(r"(anthropic/|google/gemini|openai/(gpt-4o|gpt-4\.1|gpt-5|o\d))", c.model) \
            else "pdf-text"
    if "api.openai.com" in url:
        return "native"
    return ""


class ChatAsk(BaseModel):
    cfg: Config
    question: str = ""            # câu hỏi mới
    session_id: str = ""          # phiên trò chuyện; rỗng = phiên mới
    messages: list[dict] = []     # cách gọi cũ (giao diện gửi cả lịch sử): chỉ lấy câu hỏi cuối
    page: int | None = None       # trang đang xem
    selection: str = ""           # chữ người dùng bôi đen
    side: str = ""                # bôi đen ở bản nào: "original" | "translated"
    note_ids: list[str] = []      # ghi chú gửi kèm ("💬 Hỏi lại" một ghi chú)
    context: str = "auto"         # nguồn ngữ cảnh, xem CONTEXT_MODES và chat_plan
    regenerate: bool = False      # trả lời lại câu hỏi cuối của phiên
    edit: bool = False            # thay câu hỏi cuối bằng `question` rồi hỏi lại


# ---------------- Chọn ngữ cảnh cho câu hỏi
# Luật cố định (không hỏi thêm LLM → không chậm thêm), giải thích được: giao diện hiện lý do dưới câu trả lời.
_Q_PAGE = re.compile(r"(?:\btrang|\btr\.|\bpage|\bp\.)\s*(\d{1,4})\b", re.I)
# "hình"/"bảng" đứng một mình KHÔNG đủ: tiếng Việt viết rời từng âm tiết nên "mô hình", "hình thức", "tình hình",
# "bảng thuật ngữ" đều chứa đúng chữ đó (đo 25/09/2026: "Mô hình này khác BERT thế nào?" bị gửi ảnh trang).
# Chỉ tính khi có số ("Hình 2") hoặc trong cụm chắc chắn là nói về hình/bảng của bài.
_NOT_FIG = r"(?<!mô )(?<!tình )(?<!định )(?<!địa )(?<!điển )(?<!mẫu )(?<!hữu )(?<!vô )"
_Q_FIG = re.compile(rf"(?:{_NOT_FIG}\bhình|\bfigure|\bfig\.?)\s*(\d{{1,3}})\b", re.I)
_Q_TAB = re.compile(r"(?:\bbảng|\btable|\btab\.)\s*(\d{1,3})\b", re.I)
_Q_EQ = re.compile(r"(?:công thức|phương trình|biểu thức|equation|eq\.?)\s*\(\s*(\d{1,3})\s*\)|(?:equation|eq\.)\s*(\d{1,3})\b",
                   re.I)
_Q_VISUAL = re.compile(
    r"\b(?:biểu đồ|đồ thị|sơ đồ|công thức|phương trình|ký hiệu|kí hiệu|đường cong|trục (?:x|y|hoành|tung)|"
    r"figures?|fig|tables?|charts?|plots?|graphs?|diagrams?|equations?|eq|formulas?|axis|axes)\b"
    rf"|(?:{_NOT_FIG}\bhình (?:vẽ|ảnh|minh hoạ|minh họa|này|trên|dưới|bên|đó|kia)\b|\btrong hình\b)"
    r"|\bbảng (?:này|trên|dưới|đó|kết quả|số liệu|so sánh)\b|\btrong bảng\b(?! thuật ngữ)", re.I)
_Q_GLOBAL = re.compile(r"tóm tắt|tổng quan|tổng kết|cả bài|toàn bài|toàn bộ|cả tài liệu|đóng góp|kết luận|hạn chế|"
                       r"ý chính|nội dung chính|bài (?:này|báo) (?:nói|đề xuất|làm|giải quyết)|summar|overview|"
                       r"contribution|conclusion|limitation", re.I)
_Q_TRANS = re.compile(r"bản dịch|dịch (?:sai|đúng|lại|là|thế nào|sao|như vậy)|thuật ngữ|tiếng anh|nguyên văn", re.I)
_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_VI_WORD = re.compile(r"[^\W\d_]+")


def page_texts(d: dict) -> list[str]:
    """Chữ gốc từng trang (một lần cho mỗi tài liệu): tìm chú thích hình/bảng và tìm trang liên quan."""
    if "ptext" not in d:
        d["ptext"] = [p.get_text() for p in pymupdf.open(d["src"])]
    return d["ptext"]


def _terms(text: str, vi: bool) -> list[str]:
    """Từ khoá để so trang với câu hỏi: từ tiếng Anh (bỏ từ chức năng), và cặp âm tiết tiếng Việt liền nhau."""
    low = text.lower()
    out = [w for w in _WORD.findall(low) if w not in _STOP]
    # "BERT-large" trong câu hỏi, "BERTLARGE" (chữ in nhỏ) hay "BERT large" trong bài: thêm dạng viết liền và từng phần
    out += [x for w in out if "-" in w for x in (w.replace("-", ""), *w.split("-")) if len(x) > 2 and x not in _STOP]
    if vi:
        syl = _VI_WORD.findall(low)
        out += [f"{a} {b}" for a, b in zip(syl, syl[1:])]
    return out


def related_pages(d: dict, question: str, k: int = 3) -> list[int]:
    """Trang nói về điều câu hỏi hỏi (kiểu BM25 thu gọn), tìm trên chữ gốc và bản dịch đã có. Câu hỏi tiếng Việt thì
    thuật ngữ trong bảng thuật ngữ được đổi ngược sang tiếng Anh để khớp được chữ gốc."""
    texts = page_texts(d)
    q = question
    for en, vi in (d.get("glossary") or {}).items():
        if vi and vi.lower() in question.lower():
            q += " " + en
    qt = set(_terms(q, True))
    if not qt:
        return []
    docs = []
    for n, t in enumerate(texts):
        tr = " ".join(d["trans"].get(n, {}).values())
        docs.append(collections.Counter(_terms(t, False) + _terms(untag(tr), True)))
    N_ = len(docs)
    df = collections.Counter(w for c in docs for w in qt if c[w])
    score = [(sum(math.log(1 + N_ / df[w]) * (1 + math.log(c[w])) for w in qt if c[w]), n) for n, c in enumerate(docs)]
    best = sorted((x for x in score if x[0] > 0), reverse=True)[:k]
    return [n for _, n in best]


def caption_page(d: dict, kind: str, num: str) -> int | None:
    """Trang có chú thích "Figure 2:" / "Table 3." (chỗ hình/bảng đó nằm)."""
    word = r"Figure|Fig\." if kind == "fig" else "Table"
    pat = re.compile(rf"^\s*(?:{word})\s*{num}\s*[:.|]", re.I | re.M)
    return next((n for n, t in enumerate(page_texts(d)) if pat.search(t)), None)


def equation_page(d: dict, num: str) -> int | None:
    """Trang có công thức mang số hiệu "(num)" (số hiệu đứng riêng một dòng ở lề phải, như LaTeX đặt)."""
    pat = re.compile(rf"^\s*\(\s*{num}\s*\)\s*$", re.M)
    return next((n for n, t in enumerate(page_texts(d)) if pat.search(t)), None)


def chat_plan(d: dict, ask: ChatAsk, question: str, budget: int, see: bool) -> dict:
    """Chọn nguồn ngữ cảnh cho câu hỏi. Trả về {"mode", "pages" (trang gửi chữ, quan trọng trước), "images" (trang
    gửi ảnh), "why"}. `see` = có model xem được ảnh trang."""
    n_pages = d["out"].page_count
    if ask.context == "near":         # như trước 25/09/2026 (mốc so sánh cho bench): trang đang xem + lân cận
        return {"mode": "pair" if budget >= 6000 else "source", "pages": [] if ask.page is None else [ask.page],
                "images": [], "why": "trang đang xem + lân cận"}
    q = f"{question} {ask.selection}"
    main, why = [], []
    if ask.selection.strip() and ask.page is not None:
        main.append(ask.page)
        why.append("đoạn đang bôi đen")
    for m in _Q_PAGE.finditer(question):
        if 1 <= int(m[1]) <= n_pages and int(m[1]) - 1 not in main:
            main.append(int(m[1]) - 1)
            why.append(f"câu hỏi nhắc trang {m[1]}")
    figs = [("fig", m[1]) for m in _Q_FIG.finditer(q)] + [("tab", m[1]) for m in _Q_TAB.finditer(q)]
    for kind, num in figs:
        if (p := caption_page(d, kind, num)) is not None and p not in main:
            main.append(p)
            why.append(f"{'Hình' if kind == 'fig' else 'Bảng'} {num} ở trang {p + 1}")
    for m in _Q_EQ.finditer(q):
        num = m[1] or m[2]
        if (p := equation_page(d, num)) is not None and p not in main:
            main.append(p)
            why.append(f"công thức ({num}) ở trang {p + 1}")
    if not main and ask.page is not None:
        main.append(ask.page)
    if ask.context == "pdf":   # cả file PDF gốc đi kèm; hỏi về bản dịch thì thêm bản dịch của trang liên quan
        trans = bool(_Q_TRANS.search(question)) or ask.side == "translated"
        return {"mode": "pdf", "pages": main if trans else [], "images": [],
                "why": "gửi nguyên file PDF gốc" + (" + bản dịch trang liên quan" if trans else "")}
    visual = bool(figs) or bool(_Q_VISUAL.search(q))
    mode = ask.context if ask.context in CONTEXT_MODES else "auto"
    if mode == "auto":
        if _Q_GLOBAL.search(question) and not ask.selection.strip() and not figs:
            src_tokens = sum(est_tokens(t) for t in page_texts(d))
            mode = "full" if src_tokens <= budget else "pair"
            why.append("câu hỏi về cả bài" + ("" if mode == "full" else " (bài dài: phần mở đầu, kết luận, trang liên quan)"))
            if mode == "pair":
                concl = next((int(x.split("·")[0][3:]) - 1 for x in d.get("outline", [])
                              if re.search(r"conclusion|discussion", x, re.I)), n_pages - 1)
                main = [0, concl] + [p for p in main if p not in (0, concl)]
        elif _Q_TRANS.search(question):
            mode = "pair"
            why.append("hỏi về bản dịch → chữ gốc + bản dịch")
        else:
            mode = "pair" if budget >= 6000 else "source"
    images = []
    if see and main and (ask.context == "image" or (ask.context == "auto" and visual)):
        images = main[:2]
        why.append("hỏi về hình/bảng/công thức → gửi ảnh trang")
    elif ask.context == "image":
        why.append("chưa cấu hình model xem ảnh trang → chỉ gửi chữ")
    if mode == "image":
        mode = "pair"
    rel = [p for p in related_pages(d, q) if p not in main] if mode != "full" else []
    if rel:
        why.append("trang liên quan: " + ", ".join(str(p + 1) for p in rel))
    return {"mode": mode, "pages": main + rel, "images": images, "why": "; ".join(why)}


def chat_context(d: dict, ask: ChatAsk, plan: dict, budget: int) -> tuple[str, list[int]]:
    """Phần tài liệu gửi kèm câu hỏi, cắt theo `budget` token: ghi chú được chọn ("Hỏi lại"), đoạn bôi đen, rồi các
    trang theo kế hoạch (chat_plan), rồi lan dần sang các trang bên cạnh trang chính. Trả kèm các trang đã gửi."""
    parts, used, sent = [], 0, []
    if ask.note_ids and (txt := N.context(d.get("notes", []), ask.note_ids, budget)):
        # ghi chú người đọc chọn gửi kèm: tối đa `budget` ký tự ≈ 1/4–2/5 ngân sách token (2,5–4 ký tự/token),
        # phần còn lại cho đoạn bôi đen và các trang
        parts.append(txt)
        used += est_tokens(txt)
    if ask.selection.strip():
        sel = ask.selection.strip()[:4000]
        where = "bản dịch tiếng Việt" if ask.side == "translated" else "bản gốc"
        parts.append(f"[Đoạn người dùng đang bôi đen, lấy từ {where}]\n{sel}")
        used += est_tokens(sel) + 20
    total = d["out"].page_count
    if plan["mode"] == "full":
        order = list(range(total))
    elif plan["mode"] == "pdf":       # chữ gốc đã có trong file PDF: chỉ thêm bản dịch (nếu hỏi về bản dịch)
        order = plan["pages"]
    else:
        first = plan["pages"][0] if plan["pages"] else None
        near = [] if first is None else [p for k in range(1, total) for p in (first + k, first - k) if 0 <= p < total]
        order = list(dict.fromkeys(plan["pages"] + near))
    label = {"pair": "chữ gốc + bản dịch", "source": "chữ gốc", "translation": "bản dịch", "full": "chữ gốc",
             "pdf": "bản dịch"}
    fmt = {"full": "source", "pdf": "translation"}.get(plan["mode"], plan["mode"])
    for n in order:
        if not (text := page_pair(d, n, fmt)):
            continue
        if used + (t := est_tokens(text)) > budget:
            if plan["mode"] in ("full", "pdf") or n not in plan["pages"]:
                break
            continue                      # trang theo kế hoạch quá dài: bỏ trang đó, thử trang sau
        parts.append(f"[Trang {n + 1} · {label[plan['mode']]}]\n{text}")
        used += t
        sent.append(n)
    return "\n\n".join(parts), sent


def page_image(d: dict, n: int) -> str:
    """Ảnh trang gốc (PNG, base64) cho model xem ảnh: ×1,5 đủ đọc chữ và công thức (đo 25/09/2026)."""
    png = pymupdf.open(d["src"])[n].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png")
    return base64.b64encode(png).decode()


async def chat_stream(cfg: Config, system: str, messages: list[dict], num_ctx: int = 0):
    """Trả lời của LLM theo từng mẩu chữ (Ollama trả NDJSON ở /api/chat, server kiểu OpenAI trả SSE)."""
    kind, n_ctx, slots = await server_info(cfg.base_url, cfg.model)
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    if "openrouter.ai" in cfg.base_url:
        headers |= {"HTTP-Referer": "http://localhost", "X-Title": "PDF Paper Translator"}
    # Tin nhắn có ảnh (`images`: PNG base64): API gốc của Ollama nhận thẳng "images", API kiểu OpenAI thì chữ và ảnh
    # thành các phần của `content`
    # File PDF (`pdf`: {"name", "data": base64}, chỉ gửi khi pdf_input(cfg) nhận): một phần `{"type": "file"}`
    msgs, engine = [{"role": "system", "content": system}], ""
    for m in messages:
        imgs, pdf = m.get("images") or [], m.get("pdf")
        if not imgs and not pdf:
            msgs.append({"role": m["role"], "content": m["content"]})
        elif kind == "ollama":
            msgs.append({"role": m["role"], "content": m["content"], "images": imgs})
        else:
            parts = [{"type": "text", "text": m["content"]}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}} for b64 in imgs]
            if pdf:
                engine = pdf_input(cfg)
                parts.append({"type": "file", "file": {"filename": pdf["name"],
                                                       "file_data": "data:application/pdf;base64," + pdf["data"]}})
            msgs.append({"role": m["role"], "content": parts})
    if kind == "ollama":  # API kiểu OpenAI của Ollama bỏ qua num_ctx (luôn 4096) → dùng API gốc
        url = re.sub(r"/v1/?$", "", cfg.base_url.rstrip("/")) + "/api/chat"
        body = {"model": cfg.model, "messages": msgs, "stream": True,
                "options": {"temperature": 0.3, "num_ctx": num_ctx or min(n_ctx, 16384)}}
        if cfg.disable_thinking:
            body["think"] = False
    else:
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        body = no_think(cfg, url, {"model": cfg.model, "messages": msgs, "stream": True, "temperature": 0.3})
        if engine and "openrouter.ai" in url:
            body["plugins"] = [{"id": "file-parser", "pdf": {"engine": engine}}]
    got = False           # đã nhận được chữ nào chưa (chưa thì hỏi lại được mà người dùng không thấy gì lạ)
    dropped = None        # tham số đã bỏ để hỏi lại; hỏi lại được thì nhớ cho lần sau
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=wait_for(cfg, 4096)) as client, llm_sem(cfg.base_url, slots):
                async with client.stream("POST", url, headers=headers, json=body) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")
                        if not attempt and kind != "ollama" and (k := refused_param(body, r.status_code, text)):
                            body.pop(k)                                  # model không nhận tham số này → hỏi lại
                            dropped = k
                            continue
                        raise LLMError(f"Lỗi LLM {r.status_code}: {text[:300]}")
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        ch = obj.get("choices") or [{}]
                        piece = (ch[0].get("delta") or ch[0].get("message") or {}).get("content") \
                            or (obj.get("message") or {}).get("content") or ""
                        if piece:
                            got = True
                            yield piece
                        if obj.get("done"):
                            break
            if dropped:
                _REFUSED.setdefault((url, cfg.model), set()).add(dropped)
            return
        except httpx.HTTPError as e:
            # Proxy ChatGPT OAuth với gpt-6-luna: có temperature thì trả 200, gửi một mẩu rỗng rồi NGẮT kết nối
            # (đo 24/09/2026) — chưa ra chữ nào thì bỏ temperature và hỏi lại. Chỉ lỗi ngắt kết nối này: hết giờ chờ
            # hay không kết nối được thì hỏi lại cũng vô ích.
            if (not attempt and not got and kind != "ollama" and "temperature" in body
                    and isinstance(e, httpx.RemoteProtocolError)):
                body.pop("temperature")
                dropped = "temperature"
                continue
            raise LLMError(f"Không kết nối được LLM tại {url}: {e!r}")


def doc_chats(d: dict) -> list[dict]:
    """Các phiên trò chuyện của tài liệu (nạp từ chats.json lần đầu cần tới)."""
    if "chats" not in d:
        d["chats"] = C.load(store.folder(d["id"]))
    return d["chats"]


def find_chat(d: dict, sid: str) -> dict:
    if not (s := next((x for x in doc_chats(d) if x["id"] == sid), None)):
        raise HTTPException(404, "Không tìm thấy phiên trò chuyện")
    return s


@app.post("/api/{doc_id}/chat")
async def doc_chat(doc_id: str, ask: ChatAsk):
    """Hỏi LLM về tài liệu (kèm đoạn đang bôi đen) trong một phiên trò chuyện. Server giữ lịch sử phiên: ghi câu hỏi,
    trả lời chảy về từng mẩu, xong (hoặc người dùng bấm Dừng) thì ghi câu trả lời. Header trả về: X-Session-Id (phiên,
    mới tạo nếu chưa có) và X-Context (JSON: ngữ cảnh đã gửi — chế độ, trang, ảnh, model, lý do)."""
    d = get_doc(doc_id)
    cfg0 = resolve_keys(ask.cfg)
    question = ask.question.strip() or next((m.get("content", "") for m in reversed(ask.messages)
                                             if m.get("role") == "user"), "").strip()
    sessions = doc_chats(d)
    sess = next((x for x in sessions if x["id"] == ask.session_id), None) if ask.session_id else None
    if ask.session_id and not sess:
        raise HTTPException(404, "Không tìm thấy phiên trò chuyện")
    if sess is None:
        sess = C.new()
        sessions.append(sess)
    msgs = sess["messages"]
    if ask.regenerate or ask.edit:
        while msgs and msgs[-1]["role"] == "assistant":
            msgs.pop()
        if ask.regenerate:
            if not msgs:
                raise HTTPException(400, "Phiên chưa có câu hỏi nào")
            last = msgs[-1]           # hỏi lại đúng như lần trước: cùng đoạn bôi đen, cùng trang
            question = last["content"]
            ask = ask.model_copy(update={"selection": last.get("quote", ""), "page": last.get("page"),
                                         "side": last.get("side", ""), "note_ids": last.get("note_ids", [])})
        elif msgs:
            msgs.pop()
    if not question:
        raise HTTPException(400, "Chưa có câu hỏi")
    if not ask.regenerate:
        msgs.append(C.message("user", question, quote=ask.selection.strip()[:4000], page=ask.page,
                              side=ask.side, note_ids=ask.note_ids, context=ask.context))
    sess["title"] = sess.get("title") or C.title_of(question)
    user_msg = msgs[-1]

    see = see_cfg(cfg0)
    if "outline" not in d:
        src = pymupdf.open(d["src"])
        d["outline"] = await asyncio.to_thread(doc_outline, src)
        # Tên bài vốn chỉ được đặt khi lập bảng thuật ngữ; tắt bảng tự lập thì phần trò chuyện mất luôn tên bài
        d.setdefault("title", await asyncio.to_thread(doc_title, src))
    gloss, outline = glossary_text(cfg0, d), d["outline"]
    base_system = "\n\n".join(x for x in (
        CHAT_PROMPT,
        f'Tên bài: "{d["title"]}"' if d.get("title") else "",
        "Bảng thuật ngữ (dùng đúng cách dịch này):\n" + gloss if gloss else "",
        "Mục lục bài:\n" + "\n".join(outline) if outline else "",
    ) if x)

    async def limits(c: Config, n_img: int = 0) -> tuple[int, int]:
        """(ngữ cảnh gửi server, ngân sách token cho phần tài liệu). Server tự host: num_ctx lớn là tốn RAM/VRAM → 16k;
        API ngoài thì theo ngữ cảnh thật của model (tối đa 128k). Mỗi ảnh trang tính ~1500 token."""
        kind, n_ctx, _ = await server_info(c.base_url, c.model)
        ctx_ = min(n_ctx, 16384) if kind in ("ollama", "llamacpp") else min(n_ctx, 131072)
        return ctx_, max(int(0.55 * ctx_) - est_tokens(base_system) - 1500 * n_img, 400)

    async def prepare(plan: dict, c: Config) -> tuple[str, list[dict], int, dict]:
        """System prompt, lịch sử (câu cuối kèm ngữ cảnh, ảnh), ngữ cảnh gửi server, và thông tin ngữ cảnh cho giao diện."""
        ctx_, budget = await limits(c, len(plan["images"]))
        if plan["mode"] != "full":    # không hỏi cả bài: trang chính + liên quan + lân cận là đủ, gửi cả bài chỉ làm
            budget = min(budget, CHAT_BUDGET)   # chậm và loãng câu trả lời (model 128k+ sẽ nhận nguyên bài)
        ctxt, sent = await asyncio.to_thread(chat_context, d, ask, plan, budget)
        hist = C.history(sess)
        if ctxt:
            hist[-1] = {**hist[-1], "content": f"{ctxt}\n\n[Câu hỏi]\n{hist[-1]['content']}"}
        system_ = base_system
        if plan["mode"] == "pdf":
            data = await asyncio.to_thread(lambda: base64.b64encode(Path(d["src"]).read_bytes()).decode())
            hist[-1]["pdf"] = {"name": Path(d["name"] or "paper.pdf").stem + ".pdf", "data": data}
            system_ += ("\n\nKèm theo câu hỏi là file PDF gốc của cả bài: trả lời dựa trên file đó (chữ, hình, bảng, "
                        "công thức). Bảng thuật ngữ và bản dịch (nếu có) ở trên là của app dịch — dùng đúng cách dịch đó.")
        elif plan["images"]:
            hist[-1]["images"] = [await asyncio.to_thread(page_image, d, n) for n in plan["images"]]
            system_ += ("\n\nKèm theo câu hỏi là ảnh các trang: " + ", ".join(str(n + 1) for n in plan["images"])
                        + ". Đọc hình, bảng, công thức trực tiếp trên ảnh.")
        else:
            system_ += ("\n\nCông thức trong chữ trích từ PDF nằm trong ⟦…⟧: dạng LaTeX ($…$, $$…$$) là nhận dạng từ ảnh "
                        "công thức (tin được), còn lại chép từ glyph nên có thể lộn xộn (chỉ số trên/dưới nằm ngang hàng).")
        info_ = {"mode": plan["mode"], "pages": [n + 1 for n in sent], "images": [n + 1 for n in plan["images"]],
                 "model": c.model, "why": plan["why"]}
        if k := ctxt.count("⟦$"):
            info_["why"] += f"{'; ' if info_['why'] else ''}{k} công thức dạng LaTeX"
        if plan["mode"] == "pdf":
            info_["pdf"] = pdf_input(c)
        return system_, hist, ctx_, info_

    cfg = chat_cfg(cfg0)
    ctx0, budget0 = await limits(cfg)

    def as_text(why: str) -> dict:
        """Không gửi được file PDF: gửi chữ cả bài (chế độ "Cả bài": vừa ngữ cảnh thì cả bài, không thì cắt)."""
        p = chat_plan(d, ask.model_copy(update={"context": "full"}), question, budget0, see is not None)
        return {**p, "why": f"{why} → gửi chữ cả bài" + (f"; {p['why']}" if p["why"] else "")}

    if ask.context == "pdf" and not pdf_input(cfg):
        plan = await asyncio.to_thread(as_text, "server trò chuyện không nhận file PDF")
    elif ask.context == "pdf" and Path(d["src"]).stat().st_size > PDF_MAX_MB * 2**20:
        plan = await asyncio.to_thread(as_text, f"file PDF lớn hơn {PDF_MAX_MB} MB")
    elif ask.context == "pdf" and sum(est_tokens(t) for t in page_texts(d)) > 0.8 * ctx0 - est_tokens(base_system):
        plan = await asyncio.to_thread(as_text, f"bài dài hơn ngữ cảnh của model ({ctx0} token)")
    else:
        plan = await asyncio.to_thread(chat_plan, d, ask, question, budget0, see is not None)
    if plan["mode"] != "pdf":
        # công thức → LaTeX: chờ trang chính (tối đa FORMULA_WAIT giây, lần sau đã có sẵn), các trang khác chạy nền
        need = plan["pages"] or ([ask.page] if ask.page is not None else [])
        tasks = formulas_ready(d, need + (list(range(d["out"].page_count)) if plan["mode"] == "full" else []), cfg0)
        if tasks:
            await asyncio.wait(tasks[:2], timeout=FORMULA_WAIT)
    if plan["images"]:              # trả lời bằng model xem ảnh; ngữ cảnh chữ theo ngữ cảnh của model đó
        cfg = see
    system, history, ctx, info = await prepare(plan, cfg)
    C.save(store.folder(doc_id), sessions)

    # Lấy mẩu chữ đầu tiên TRƯỚC khi trả response: model xem ảnh lỗi ngay (429, quá tải, không nhận ảnh) thì còn kịp
    # hỏi lại bằng model trò chuyện chỉ với chữ — và header X-Context nói đúng ngữ cảnh đã dùng.
    stream, first, error = chat_stream(cfg, system, history, ctx), "", None
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        pass
    except LLMError as e:
        error = e
    if error is not None and (plan["images"] or plan["mode"] == "pdf"):
        await stream.aclose()
        if plan["mode"] == "pdf":    # server từ chối file (model không đọc được PDF, file quá lớn…) → gửi chữ
            plan = await asyncio.to_thread(as_text, f"{plan['why']}: server lỗi ({str(error)[:120]})")
        else:
            plan = {**plan, "images": [], "why": f"{plan['why']}; model xem ảnh lỗi ({str(error)[:120]}) → chỉ gửi chữ"}
        cfg = chat_cfg(cfg0)
        system, history, ctx, info = await prepare(plan, cfg)
        stream, first, error = chat_stream(cfg, system, history, ctx), "", None
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            pass
        except LLMError as e:
            error = e

    async def gen():
        acc, stopped, failed = first, True, False
        try:
            if error is not None:
                raise error
            if first:
                yield first
            async for piece in stream:
                acc += piece
                yield piece
            stopped = False
        except LLMError as e:
            failed = True
            yield f"\n\n⚠️ {e}"
        finally:                      # cả khi người dùng bấm Dừng (kết nối bị ngắt): giữ phần đã trả lời
            await stream.aclose()
            if acc.strip():
                msgs.append(C.message("assistant", acc, model=cfg.model, ctx=info, stopped=stopped and not failed))
            elif not ask.regenerate and failed and user_msg in msgs:
                msgs.remove(user_msg)  # LLM lỗi, chưa ra chữ nào: bỏ câu hỏi (giao diện trả câu hỏi về ô gõ)
            sess["updated"] = time.time()
            C.save(store.folder(doc_id), sessions)

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8", headers={
        "X-Session-Id": sess["id"], "X-Context": json.dumps(info), "Cache-Control": "no-store"})


class FormulaAsk(BaseModel):
    cfg: Config
    pages: list[int] = []         # rỗng = cả bài


@app.post("/api/{doc_id}/formulas")
async def doc_formulas(doc_id: str, ask: FormulaAsk):
    """Chép sang LaTeX công thức của các trang (chờ xong) — trò chuyện tự làm việc này cho trang cần; endpoint để
    làm trước cả bài và để kiểm tra. Trả về số công thức: đã có LaTeX / giữ glyph (nhận dạng không dùng được)."""
    d, cfg = get_doc(doc_id), resolve_keys(ask.cfg)
    if formula_cfg(cfg) is None:
        raise HTTPException(400, "Chưa bật nhận dạng công thức (⚙️ Cấu hình → Trò chuyện)")
    d.pop("flatex_fail", None)
    pages = [n for n in ask.pages if 0 <= n < d["out"].page_count] or list(range(d["out"].page_count))
    await asyncio.gather(*formulas_ready(d, pages, cfg))
    got = [v for n in pages for v in d.get("flatex", {}).get(n, {}).values()]
    return {"latex": sum(bool(v) for v in got), "glyph": sum(not v for v in got),
            "failed": "flatex_fail" in d}


@app.get("/api/{doc_id}/chats")
def list_chats(doc_id: str):
    return {"sessions": C.listing(doc_chats(get_doc(doc_id)))}


@app.get("/api/{doc_id}/chats/{sid}.md")
def chat_md(doc_id: str, sid: str):
    d = get_doc(doc_id)
    s = find_chat(d, sid)
    name = re.sub(r'[\\/:*?"<>|]+', " ", s.get("title") or "tro-chuyen").strip()[:60] or "tro-chuyen"
    return Response(C.markdown(s, d.get("title") or d["name"]), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.md"})


@app.get("/api/{doc_id}/chats/{sid}")
def get_chat(doc_id: str, sid: str):
    return find_chat(get_doc(doc_id), sid)


class ChatRename(BaseModel):
    title: str


@app.patch("/api/{doc_id}/chats/{sid}")
def rename_chat(doc_id: str, sid: str, body: ChatRename):
    d = get_doc(doc_id)
    s = find_chat(d, sid)
    s["title"] = " ".join(body.title.split())[:120]
    C.save(store.folder(doc_id), doc_chats(d))
    return C.summary(s)


@app.delete("/api/{doc_id}/chats/{sid}")
def delete_chat(doc_id: str, sid: str):
    d = get_doc(doc_id)
    find_chat(d, sid)
    d["chats"] = [x for x in doc_chats(d) if x["id"] != sid]
    C.save(store.folder(doc_id), d["chats"])
    return {"deleted": sid}


SUMMARY_PROMPT = """Bạn tổng hợp một cuộc trò chuyện giữa người đọc và trợ giảng về một bài báo khoa học thành GHI CHÚ học tập bằng tiếng Việt, để người đọc xem lại sau.
Viết Markdown, ngắn gọn, chỉ giữ điều đáng nhớ; theo đúng các mục sau (bỏ mục không có gì):
## Câu hỏi chính
## Điều rút ra
(các ý đã được giải thích/kết luận, mỗi ý một gạch đầu dòng; ghi số trang (tr. N) nếu cuộc trò chuyện có nhắc)
## Khái niệm, công thức
(công thức viết bằng LaTeX trong $...$ hoặc $$...$$)
## Còn bỏ ngỏ
(câu hỏi chưa được trả lời rõ, việc cần đọc thêm)
Không bịa thêm điều cuộc trò chuyện không nói. Không mở bài, không kết bài."""


@app.post("/api/{doc_id}/chats/{sid}/summarize")
async def summarize_chat(doc_id: str, sid: str, cfg: Config):
    """Tổng hợp một phiên trò chuyện thành một ghi chú của bài (nguồn LLM, sửa được như ghi chú thường)."""
    d = get_doc(doc_id)
    s = find_chat(d, sid)
    if not any(m["role"] == "assistant" for m in s["messages"]):
        raise HTTPException(400, "Phiên chưa có câu trả lời nào để tổng hợp")
    c = chat_cfg(resolve_keys(cfg))
    kind, n_ctx, _ = await server_info(c.base_url, c.model)
    ctx = min(n_ctx, 16384) if kind in ("ollama", "llamacpp") else min(n_ctx, 131072)
    text = C.transcript(s, limit_chars=int(0.5 * ctx * 3))
    title = d.get("title") or d["name"]
    out = ""
    try:
        async for piece in chat_stream(c, SUMMARY_PROMPT, [{"role": "user", "content":
                                       f'Bài báo: "{title}"\n\nCuộc trò chuyện:\n\n{text}'}], ctx):
            out += piece
    except LLMError as e:
        raise HTTPException(502, str(e))
    if not out.strip():
        raise HTTPException(502, "LLM không trả lời")
    notes = d.setdefault("notes", [])
    meta = {"question": f"Tổng hợp phiên trò chuyện: {s.get('title') or ''}", "model": c.model, "session": sid}
    # tổng hợp lại một phiên (đã hỏi thêm) → thay ghi chú tổng hợp cũ của phiên đó, không thêm bản trùng
    old = next((x for x in notes if x.get("meta", {}).get("session") == sid), None)
    note = N.upsert(notes, N.Note(id=old["id"] if old else "", text=out.strip(), source="llm", meta=meta),
                    d["out"].page_count)
    if old:
        note["meta"] = meta
    store.save_state(d)
    return note


@app.get("/api/{doc_id}/download")
def download(doc_id: str, which: str = "translated", sync: bool = True):
    """PDF tải về kèm tô vàng và ghi chú. `which="original"`: bản gốc có tô vàng (tắt đồng bộ vệt tô thì vệt tô ở bản
    gốc chỉ có ở đây). `sync=False`: không vẽ phần tương ứng của vệt tô bên bản kia."""
    d = get_doc(doc_id)
    side = "original" if which == "original" else "translated"
    path = store.folder(doc_id) / f"download-{side}.pdf"
    # garbage=4: gộp cả stream trùng nội dung — mỗi trang dịch nhúng lại nguyên font tiếng Việt và font công thức,
    # garbage=3 chỉ gộp đối tượng thường nên PDF 2 MB ra 6–20 MB; garbage=4 còn ≈ bản gốc + vài trăm KB.
    # Cắt font (MuPDF, không cần fontTools) trên bản sao để không đụng tài liệu đang dịch dở: chỉ giữ ký tự đã dùng —
    # Times New Roman của macOS ~800 KB/kiểu chữ còn vài chục KB (đo 19/09/2026: ảnh trang và chữ trích giống hệt)
    doc = pymupdf.open("pdf", d["out"].tobytes(garbage=4) if side == "translated" else d["src"].read_bytes())
    by_mark, loose = N.plan(d["marks"], d.get("notes", []), side, sync)
    for n, marks in d["marks"].items():    # chữ đã tô vàng → annotation highlight thật trong PDF
        # vệt tô ở chính bản này thì lấy khung của nó, tô ở bản kia thì lấy khung cụm/câu tương ứng (nếu đồng bộ)
        if n >= doc.page_count or not any(N.mark_rects(m, side, sync) for m in marks):
            continue
        page = doc[n]                      # phải giữ tham chiếu trang: trang bị thu hồi là annot "not bound"
        for m in marks:
            for k, r in enumerate(N.mark_rects(m, side, sync)):
                annot = page.add_highlight_annot(pymupdf.Rect(r))
                annot.set_colors(stroke=(1, 0.88, 0.25))
                annot.set_opacity(0.4)     # chữ bên dưới vẫn đọc được
                if k == 0 and m["id"] in by_mark:   # ghi chú của vệt tô → nội dung popup của highlight đầu tiên
                    annot.set_info(content=by_mark[m["id"]], title="VietPaper")
                annot.update()
    N.add_loose(doc, loose)                # ghi chú của trang / cả bài → biểu tượng ghi chú ở lề trên
    try:
        if side == "translated":           # bản gốc: font của tác giả, giữ nguyên
            doc.subset_fonts()
    except Exception as e:  # noqa: BLE001 — tính năng mới của MuPDF; lỗi thì nhúng nguyên font như trước
        print(f"[font] không cắt được font ({e!r}) → nhúng nguyên font")
    doc.save(path, garbage=4, deflate=True)
    name = Path(d["name"] or "paper.pdf").stem + ("_vi.pdf" if side == "translated" else "_goc_to_vang.pdf")
    return FileResponse(path, filename=name, media_type="application/pdf")


@app.post("/api/models")
async def list_models(cfg: Config):
    cfg.api_key = remember(cfg.base_url, cfg.api_key)
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(cfg.base_url.rstrip("/") + "/models", headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, repr(e))
    if r.status_code >= 400:
        raise HTTPException(502, r.text[:300])
    data = r.json().get("data", [])
    # llama.cpp router còn liệt kê model tự quét từ HF cache (--hf-repo) và mục "default"
    # không trỏ tới file nào -> chỉ giữ preset có --model. Server khác (không có status.args) giữ nguyên.
    presets = [m for m in data if "--model" in ((m.get("status") or {}).get("args") or [])]
    out = [model_meta(m) for m in (presets or data) if m.get("id")]
    MODEL_META[cfg.base_url.rstrip("/")] = {m["id"]: m for m in out}
    save_model_meta()
    return {"models": sorted(out, key=lambda m: m["id"])}


# base_url → {model id → model_meta}: lần gần nhất tải danh sách model (để biết model nào đọc được ảnh, ngữ cảnh bao
# nhiêu). Lưu ra đĩa: giao diện nhớ danh sách model 24 giờ nên sau khi khởi động lại app có thể chưa hỏi lại server
MODEL_META_FILE = DATA / "model_meta.json"
MODEL_META: dict[str, dict[str, dict]] = {}


def load_model_meta() -> None:
    try:
        MODEL_META.update({b: v for b, v in json.loads(MODEL_META_FILE.read_text()).items() if isinstance(v, dict)})
    except (OSError, ValueError):
        pass


def save_model_meta() -> None:
    """Chỉ giữ model có thông tin (ngữ cảnh / đọc ảnh): OpenRouter liệt kê vài trăm model."""
    keep = {b: {k: {"id": k, "ctx": m.get("ctx"), "vision": m.get("vision")} for k, m in ms.items()
                if m.get("ctx") or m.get("vision") is not None} for b, ms in MODEL_META.items()}
    try:
        tmp = MODEL_META_FILE.with_name(MODEL_META_FILE.name + ".tmp")
        tmp.write_text(json.dumps({b: v for b, v in keep.items() if v}))
        os.replace(tmp, MODEL_META_FILE)
    except OSError as e:
        print(f"[models] không lưu được {MODEL_META_FILE.name}: {e!r}")


load_model_meta()


def model_meta(m: dict) -> dict:
    """Thông tin một model trong /models để hiện nhãn ở ô chọn model: ngữ cảnh, có đọc ảnh không, có miễn phí không.
    Chỉ OpenRouter (và vài API khác) cho biết; không biết thì để None — không đoán."""
    arch, price = m.get("architecture") or {}, m.get("pricing") or {}
    mods = arch.get("input_modalities")
    try:
        free = float(price["prompt"]) == 0 and float(price["completion"]) == 0 if price else None
    except (KeyError, TypeError, ValueError):
        free = None
    return {"id": m["id"], "name": m.get("name") or "",
            "ctx": m.get("context_length") or (m.get("top_provider") or {}).get("context_length"),
            "vision": ("image" in mods) if isinstance(mods, list) else None,
            "free": True if m["id"].endswith(":free") else free}


class NoCache(StaticFiles):
    """Sửa static/app.js hay index.html xong, tải lại trang là thấy ngay: "no-cache" bắt trình duyệt hỏi lại server
    mỗi lần (vẫn trả 304 nếu file không đổi nên không tốn thêm gì)."""

    def file_response(self, *args, **kw):
        resp = super().file_response(*args, **kw)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", NoCache(directory=BASE / "static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
