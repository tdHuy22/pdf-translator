#!/bin/sh
# Bật / tắt / khởi động lại app chạy nền (log ghi vào app.log).
#   ./restart.sh            khởi động lại (mặc định)
#   ./restart.sh start|stop|status
#   ./restart.sh logs       xem log đang chạy (Ctrl-C để thoát)
cd "$(dirname "$0")" || exit 1
PORT=8000
URL="http://127.0.0.1:$PORT"
LOG=app.log

# PID của app: tiến trình đang nghe cổng 8000 và đúng là python chạy app.py (không đụng tiến trình khác)
pids() {
  if command -v lsof >/dev/null 2>&1; then
    for pid in $(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null); do
      ps -o command= -p "$pid" 2>/dev/null | grep -q "app\.py" && echo "$pid"
    done
  else
    pgrep -f "[a]pp\.py" 2>/dev/null
  fi
}

stop() {
  found=$(pids)
  [ -z "$found" ] && { echo "App không chạy"; return 0; }
  echo "Dừng app (PID $(echo $found))…"
  for pid in $found; do kill "$pid" 2>/dev/null; done
  i=0
  while [ -n "$(pids)" ] && [ $i -lt 20 ]; do sleep 0.5; i=$((i + 1)); done   # chờ tối đa 10 giây
  for pid in $(pids); do echo "Không chịu tắt → buộc dừng $pid"; kill -9 "$pid" 2>/dev/null; done
  echo "Đã dừng"
}

start() {
  [ -n "$(pids)" ] && { echo "App đang chạy sẵn ở $URL"; return 0; }
  echo "Khởi động app…"
  nohup ./run.sh >>"$LOG" 2>&1 &
  i=0
  while [ $i -lt 120 ]; do                                                    # chờ tối đa 60 giây (lần đầu còn cài thư viện)
    if curl -sf -o /dev/null "$URL"; then
      echo "Đã chạy: $URL (PID $(pids | tr '\n' ' ')· log: $LOG)"
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  echo "Khởi động thất bại, 20 dòng cuối của $LOG:"
  tail -20 "$LOG"
  return 1
}

case "${1:-restart}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status)
    found=$(pids)
    if [ -n "$found" ]; then
      echo "Đang chạy: $URL (PID $(echo $found))"
      curl -sf -o /dev/null "$URL" && echo "Trang web trả lời bình thường" || echo "Nhưng trang web không trả lời!"
    else
      echo "Không chạy"
    fi ;;
  logs) tail -f "$LOG" ;;
  *) echo "Dùng: $0 [start|stop|restart|status|logs]"; exit 1 ;;
esac
