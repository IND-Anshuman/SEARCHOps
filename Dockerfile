# SEARCHOps image: API + the built web app, served from one origin.
#
# Three stages:
#   1. web     — builds the React app (the Clerk publishable key is public and is
#                compiled in, so it is a build argument).
#   2. builder — resolves Python dependencies from uv.lock.
#   3. runtime — no build toolchain; runs as an unprivileged user.
#
# Tool profile (build argument TOOL_PROFILE, also the runtime default):
#   api   (default) — search and page text through paid APIs (Tavily). No
#                     browser, no free scraping packages: the small image.
#   local           — adds the free self-hosted stack (Crawl4AI, Playwright +
#                     Chromium, stealth HTTP, PDF parsing). About 1.5 GB more.
#
# Build:
#   docker build -t searchops \
#     --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_live_... .
#   # with the free tools:
#   docker build -t searchops-local --build-arg TOOL_PROFILE=local \
#     --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_live_... .
#
# Run (see .env.example for the full list; production refuses to start without
# authentication configured):
#   docker run -p 8000:8000 --env-file .env \
#     -e ALLOWED_HOSTS=app.example.com -e API_ALLOWED_ORIGINS=https://app.example.com searchops

ARG TOOL_PROFILE=api

# ── Stage 1: web app ─────────────────────────────────────────────────────────
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
# Empty API base = same origin: the API serves this app, so calls and the
# WebSocket go to whatever host the page was loaded from.
# Sign-in is paused unless VITE_AUTH_ENABLED=true; then the Clerk key is required.
ARG VITE_CLERK_PUBLISHABLE_KEY=""
ARG VITE_AUTH_ENABLED=false
ENV VITE_API_BASE_URL="" \
    VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY} \
    VITE_AUTH_ENABLED=${VITE_AUTH_ENABLED}
RUN [ "$VITE_AUTH_ENABLED" != "true" ] || test -n "$VITE_CLERK_PUBLISHABLE_KEY" || (echo "Build with --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_..." && exit 1)
RUN npm run build

# ── Stage 2: Python dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv is the project's package manager (see uv.lock); pin the version so image
# builds stay reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first so the layer is cached independently of source changes.
# The free-tool packages are the optional `local-tools` extra.
ARG TOOL_PROFILE
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable --no-dev \
        $([ "$TOOL_PROFILE" = "local" ] && echo --extra local-tools)

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="SEARCHOps" \
      org.opencontainers.image.description="Autonomous research with per-claim citations and verification"

ARG TOOL_PROFILE
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TOOL_PROFILE=${TOOL_PROFILE}

# Unprivileged user. Only /data is written to (finished reports); the code and
# the venv stay root-owned and read-only to the app. (A `chown -R` of /app used
# to copy the whole venv and browser into a second 1.9 GB layer.)
RUN useradd --create-home --shell /usr/sbin/nologin searchops \
    && mkdir -p /data/reports \
    && chown -R searchops:searchops /data

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY config/ ./config/
COPY --from=web /web/dist ./frontend/dist

# Local profile only: Playwright's Chromium plus the OS libraries it declares
# (the browser tiers and the PDF export need them). World-readable, so no chown.
RUN if [ "$TOOL_PROFILE" = "local" ]; then playwright install --with-deps chromium; fi

USER searchops

ENV APP_ENV=production \
    SEARCHOPS_REPORT_DIR=/data/reports
VOLUME ["/data"]

EXPOSE 8000

# Liveness only: it must not fail because an optional dependency is down.
# Python rather than curl, so the image needs no extra OS package.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4)"]

# --proxy-headers trusts X-Forwarded-* from FORWARDED_ALLOW_IPS (uvicorn reads
# that variable; default is 127.0.0.1). Behind a reverse proxy on another
# address, set it to the proxy's address — not "*" on a publicly exposed port.
CMD ["uvicorn", "searchops.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
