FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Google Chrome rather than the Chromium Playwright bundles: Chromium names
# itself HeadlessChrome to every site it visits, and bot detection refuses it.
# Xvfb supplies the display Chrome needs to run headful, which is what drops
# that name from its user agent and client hints.
RUN uv run playwright install --with-deps chrome \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/data

EXPOSE 8000

ENV TABVIO_HEADLESS=false \
    TABVIO_BROWSER_CHANNEL=chrome

CMD ["sh", "-c", "xvfb-run -a --server-args='-screen 0 1365x768x24' uvicorn tabvio.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
