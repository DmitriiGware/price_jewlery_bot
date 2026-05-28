FROM aiogram/telegram-bot-api:latest

USER root
WORKDIR /app

RUN apk add --no-cache python3 py3-pip ca-certificates

COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/start.sh \
    && mkdir -p /var/lib/telegram-bot-api /tmp/telegram-bot-api /app/models \
    && chown -R telegram-bot-api:telegram-bot-api /var/lib/telegram-bot-api /tmp/telegram-bot-api

ENV PATH="/opt/venv/bin:$PATH" \
    TELEGRAM_WORK_DIR="/var/lib/telegram-bot-api" \
    TELEGRAM_TEMP_DIR="/tmp/telegram-bot-api" \
    TELEGRAM_HTTP_PORT="8081" \
    TELEGRAM_LOCAL="1" \
    TELEGRAM_API_BASE_URL="http://127.0.0.1:8081" \
    TELEGRAM_API_LOCAL_MODE="true"

ENTRYPOINT ["/app/start.sh"]
