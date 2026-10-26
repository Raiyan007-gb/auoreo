# syntax=docker/dockerfile:1
# AUOREO server + web UI on one port (W3-A). Build: `docker build -t auoreo .`
# Run:  docker run -p 4096:4096 -e AUOREO_SERVER_PASSWORD=... -v auoreo-data:/data ghcr.io/<owner>/auoreo
#
# AUOREO_SERVER_PASSWORD is REQUIRED: the image binds 0.0.0.0 and `auoreo serve` refuses to
# bind a non-loopback address without it (exit 2).
#
# Extras: the venv gets `--extra rtc` only. Add more with the EXTRAS build arg, e.g.
#   docker build --build-arg EXTRAS="--extra rtc --extra redis --extra otel" -t auoreo .
# Heavy stacks (rerank-local/torch, perception models) stay out of the default image.

ARG PYTHON_VERSION=3.12

# ---- web: build web/dist once ----
FROM node:22-alpine AS web
WORKDIR /w
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- py: build the venv with uv (deps layer first, then the project, non-editable) ----
FROM ghcr.io/astral-sh/uv:0.11.32-python3.12-trixie-slim AS py
ARG EXTRAS="--extra rtc"
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_INSTALLER_METADATA=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project --no-editable ${EXTRAS}
COPY . /app
# the wheel build hook packs the definitions + web/dist into auoreo/_bundled (fails without dist)
COPY --from=web /w/dist /app/web/dist
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable ${EXTRAS}

# ---- runtime: venv only, non-root, data on a volume ----
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime
LABEL org.opencontainers.image.title="auoreo" \
      org.opencontainers.image.description="AUOREO filesystem-native agent harness (server + web UI)" \
      org.opencontainers.image.licenses="NOASSERTION"
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    AUOREO_HOME=/data \
    AUOREO_HOST=0.0.0.0 \
    AUOREO_PORT=4096
# ripgrep backs grep/glob/codebase_map (the tools fall back to Python without it, slower)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 auoreo \
    && useradd --system --uid 10001 --gid auoreo --home-dir /data --shell /usr/sbin/nologin auoreo \
    && mkdir -p /data /work \
    && chown auoreo:auoreo /data /work \
    && chmod 0700 /data
COPY --from=py /app/.venv /app/.venv
USER auoreo
WORKDIR /work
VOLUME ["/data"]
EXPOSE 4096
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('AUOREO_PORT','4096'), timeout=4)"]
ENTRYPOINT ["auoreo", "serve"]
CMD []
