#!/usr/bin/env sh
set -eu

if [ -z "${TELEGRAM_API_ID:-}" ] || [ -z "${TELEGRAM_API_HASH:-}" ]; then
  echo "error: TELEGRAM_API_ID and TELEGRAM_API_HASH are required"
  exit 1
fi

export TELEGRAM_WORK_DIR="${TELEGRAM_WORK_DIR:-/var/lib/telegram-bot-api}"
export TELEGRAM_TEMP_DIR="${TELEGRAM_TEMP_DIR:-/tmp/telegram-bot-api}"
export TELEGRAM_HTTP_PORT="${TELEGRAM_HTTP_PORT:-8081}"
export TELEGRAM_LOCAL="${TELEGRAM_LOCAL:-1}"
export TELEGRAM_API_BASE_URL="${TELEGRAM_API_BASE_URL:-http://127.0.0.1:8081}"
export TELEGRAM_API_LOCAL_MODE="${TELEGRAM_API_LOCAL_MODE:-true}"

mkdir -p "$TELEGRAM_WORK_DIR" "$TELEGRAM_TEMP_DIR"

/docker-entrypoint.sh &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

python - <<'PY'
import os
import socket
import sys
import time

host = "127.0.0.1"
port = int(os.environ.get("TELEGRAM_HTTP_PORT", "8081"))

for _ in range(60):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        if sock.connect_ex((host, port)) == 0:
            sys.exit(0)
    time.sleep(1)

sys.exit("telegram-bot-api did not start on 127.0.0.1:%s" % port)
PY

python main.py
