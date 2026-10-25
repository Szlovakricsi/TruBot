FROM python:3.13-slim

ARG TRUBOT_VERSION=dev

LABEL org.opencontainers.image.title="TruBot" \
      org.opencontainers.image.description="Telegram monitoring and management for TrueNAS SCALE" \
      org.opencontainers.image.source="https://github.com/Szlovakricsi/TruBot" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRUBOT_VERSION=${TRUBOT_VERSION} \
    STATE_FILE=/data/state.json

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py /app/bot.py
RUN mkdir -p /data
VOLUME ["/data"]

CMD ["python", "/app/bot.py"]
