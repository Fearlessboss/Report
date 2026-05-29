# ╔══════════════════════════════════════════════════════╗
# ║   🛡️  ILLEGAL CONTENT DETECTOR BOT — Dockerfile     ║
# ║   Python 3.11 slim | Telethon + PTB + httpx          ║
# ╚══════════════════════════════════════════════════════╝

FROM python:3.11-slim

# ───────────────────────────────────────────────
# Metadata
# ───────────────────────────────────────────────
LABEL maintainer="Owner"
LABEL description="Illegal Content Detector Bot — Telethon + python-telegram-bot + OpenRouter AI"

# ───────────────────────────────────────────────
# Environment
# ───────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

# ───────────────────────────────────────────────
# System deps (minimal — for cryptg / Pillow / tzdata)
# ───────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
        libssl-dev \
        tzdata \
        ca-certificates \
        curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ───────────────────────────────────────────────
# Workdir
# ───────────────────────────────────────────────
WORKDIR /app

# ───────────────────────────────────────────────
# Python dependencies
# ───────────────────────────────────────────────
COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ───────────────────────────────────────────────
# Copy bot source
# ───────────────────────────────────────────────
COPY . .

# ───────────────────────────────────────────────
# Persistent data dir (session + sudo + logs)
# Mount this as a volume in docker run / compose
# ───────────────────────────────────────────────
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# ───────────────────────────────────────────────
# Non-root user (security best practice)
# ───────────────────────────────────────────────
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# ───────────────────────────────────────────────
# Healthcheck (optional — checks Python process)
# ───────────────────────────────────────────────
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "python" > /dev/null || exit 1

# ───────────────────────────────────────────────
# Run bot
# ───────────────────────────────────────────────
CMD ["python", "-u", "bot.py"]
