#!/bin/sh
# Cài đặt (lần đầu) và chạy ứng dụng tại http://127.0.0.1:8000
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python app.py
