# Build stage: install dependencies with uv
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Runtime stage: needs Playwright browsers for Crawl4AI
FROM python:3.12-slim

# Playwright/Chromium system dependencies + Xvfb for headless display
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libwayland-client0 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY *.py ./

ENV PATH="/app/.venv/bin:$PATH"

RUN adduser --disabled-password appuser
USER appuser

ENV PLAYWRIGHT_BROWSERS_PATH=/tmp/playwright-browsers
RUN playwright install chromium
COPY --chown=appuser docker-entrypoint.sh /app/
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
