FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev

FROM python:3.13-slim

ARG VERSION="v3.64.0" # x-release-please-version
ARG BUILD_DATE
ARG VCS_REF
ARG VCS_REPO_URL="https://github.com/haku4130/remnawave-telegram-bot"

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY . .

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VERSION=${VERSION} \
    BUILD_DATE=${BUILD_DATE} \
    VCS_REF=${VCS_REF}

LABEL org.opencontainers.image.title="Bedolaga RemnaWave Bot" \
    org.opencontainers.image.description="Telegram bot for RemnaWave VPN service" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="${VCS_REPO_URL}" \
    org.opencontainers.image.url="${VCS_REPO_URL}"

CMD ["python", "main.py"]
