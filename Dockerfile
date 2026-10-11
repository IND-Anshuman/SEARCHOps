# SEARCHOps API image.
#
# Multi-stage: the builder resolves dependencies from uv.lock so the runtime
# image never contains the build toolchain. Playwright browsers are installed
# at build time because the scraping tier needs a real Chromium.
#
# NOTE: the default docker-compose profile does NOT build this — developers run
# the API on the host for live reload. Build explicitly:
#   docker compose --profile api build api

# ── Stage 1: dependencies ────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv is the project's package manager (see uv.lock); pin the version so image
# builds stay reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first so the layer is cached independently of source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="SEARCHOps API" \
      org.opencontainers.image.description="Enterprise Autonomous Technology Intelligence Platform" \
      org.opencontainers.image.source="https://github.com/searchops/searchops"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Playwright's Chromium needs these system libraries; the scraping tier fails at
# runtime with an opaque "browser not found" without them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

# Install Playwright's browser plus the OS deps it declares.
RUN playwright install --with-deps chromium

# Run as an unprivileged user: the API never needs root.
RUN useradd --create-home --shell /usr/sbin/nologin searchops \
    && chown -R searchops:searchops /app
USER searchops

EXPOSE 8000

# Config is read from the environment (see src/searchops/config).
# APP_ENV selects the profile; it is the only env var the config layer reads.
# NOTE: API auth is enforced in every environment, including development, so
# the container must be given APP_SECRET_KEY or SEARCHOPS_API_KEY.
ENV APP_ENV=development

# --proxy-headers: correct client IPs behind the compose network.
CMD ["uvicorn", "searchops.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
