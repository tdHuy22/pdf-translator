"""Ví dụ server OCR tuỳ chỉnh (dùng RapidOCR) — thay bằng PaddleOCR, Surya, docTR... tuỳ ý.

Chạy:  .venv/bin/python examples/custom_ocr_server.py   → URL: http://127.0.0.1:9000/ocr
Hợp đồng: POST multipart file=<ảnh>, lang=<mã>  →  {"lines": [{"bbox": [x0,y0,x1,y1], "text": "..."}]} (pixel)
"""
from fastapi import FastAPI, Form, UploadFile
from rapidocr import LangRec, RapidOCR

app = FastAPI()
engine = RapidOCR(params={"Rec.lang_type": LangRec.EN})


@app.post("/ocr")
async def ocr(file: UploadFile, lang: str = Form("eng")):
    res = engine(await file.read())
    lines = []
    for box, text in zip(res.boxes if res.boxes is not None else [], res.txts or []):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        lines.append({"bbox": [min(xs), min(ys), max(xs), max(ys)], "text": text})
    return {"lines": lines}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)
