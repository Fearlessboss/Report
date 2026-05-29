# ╔══════════════════════════════════════════════════════╗
# ║              TELEGRAM BOT — Dockerfile              ║
# ╚══════════════════════════════════════════════════════╝

FROM python:3.11-slim

# ───────────────────────────────────────────────
# Environment
# ───────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

# ───────────────────────────────────────────────
# System dependencies
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
# Work directory
# ───────────────────────────────────────────────
WORKDIR /app

# ───────────────────────────────────────────────
# Copy requirements first
# ───────────────────────────────────────────────
COPY requirements.txt .

# ───────────────────────────────────────────────
# Install Python dependencies
# ───────────────────────────────────────────────
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    pip install aiohttp

# ───────────────────────────────────────────────
# Copy source code
# ───────────────────────────────────────────────
COPY . .

# ───────────────────────────────────────────────
# Create persistent data directory
# ───────────────────────────────────────────────
RUN mkdir -p /app/data

VOLUME ["/app/data"]

# ───────────────────────────────────────────────
# Non-root user
# ───────────────────────────────────────────────
RUN useradd -m botuser && \
    chown -R botuser:botuser /app

USER botuser

# ───────────────────────────────────────────────
# Healthcheck
# ───────────────────────────────────────────────
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
CMD pgrep -f "python" > /dev/null || exit 1

# ───────────────────────────────────────────────
# Start bot
# ───────────────────────────────────────────────
CMD ["python", "-u", "bot.py"]
