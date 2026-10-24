---
name: docker
description: Package the platform as containers — multi-stage Dockerfile best practices, non-root, healthchecks, multi-arch, GHCR tags, Docker Compose for local dev. Use when working in containers/ in this repo.
---

# Docker — containers/ (GHCR + Compose)

Delivers the full "server + web UI on one port" experience: `docker run -p 4096:4096 ghcr.io/<org>/agent-platform:<tag>` (plan.md Part J channel). Multi-arch builds run in CI (`containers.yml`).

## Multi-stage Dockerfile (containers/Dockerfile)

Keep runtime image small and non-root (open-code + plan.md Part K security):

```dockerfile
# ---- build stage: build web UI one time ----
FROM node:22-alpine AS web
WORKDIR /w
COPY web/app/package.json web/app/package-lock.json ./
RUN npm ci
COPY web/app/ ./
RUN npm run build                     # → dist/

FROM node:22-alpine AS tsdeps         # (optional) SDK/launcher deps
# ...

# ---- python deps stage (uv official Docker pattern) ----
FROM ghcr.io/astral-sh/uv:0.12.3-python3.12-trixie-slim AS py   # ⚠ pin the tag/SHA (moving tags = unreproducible)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# 1st sync: deps only (cache mount + --no-dev + --no-install-project per uv guide)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project --no-editable
COPY . /app
# 2nd sync: install THIS project into the venv (--no-editable → runtime needs only /app/.venv)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# ---- runtime (venv only; no project source) ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 WORKDIR=/app
WORKDIR /app
COPY --from=py /app/.venv /app/.venv      # venv WITH the `agent` console script installed
COPY --from=web /w/dist /app/web/app/dist
RUN useradd --uid 10001 appuser          # non-root
USER appuser
EXPOSE 4096
HEALTHCHECK --interval=30s --timeout=3s CMD /app/.venv/bin/python -m agent_cli health || exit 1   # real endpoint
CMD ["/app/.venv/bin/agent", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
```

## Compose (containers/docker-compose.yml)

Local full-stack dev: `api` + (optional) `postgres` (pgvector) + `redis` (for Phase 3 fan-out). Keep the app usable with zero extras (SQLite default) so `docker compose up` just works.

```yaml
services:
  api:
    build: .
    ports: ["4096:4096"]
    environment:
      - AGENT_PLATFORM_SERVER_PASSWORD=${AGENT_PLATFORM_SERVER_PASSWORD:-}
      - DATABASE_URL=sqlite+aiosqlite:////data/agent.db
    volumes: ["agent-data:/data"]
volumes: { agent-data: {} }
```

## Image + tags (GHCR, containers.yml)

- Tag with version: `ghcr.io/<org>/agent-platform:v1.2.3`, plus `latest`; use `docker/build-push-action` with platforms `linux/amd64,linux/arm64` (QEMU + buildx) — plan.md Part J.
- `provenance: true` (SLSA/attestations) on push; registry login via `GITHUB_TOKEN` for GHCR.
- Pin base images by digest where practical; enable Dependabot for base-image bumps.

## Best practices (Context7 `/rcjsuen/dockerfile-ast`, Hadolint `/hadolint/hadolint`)

- Layer caching: `COPY` dependency manifests + `install/ci` before copying source (invalidates on code change, not README).
- Don't bake **model weights or API keys** into the image; read keys from env at runtime (plan.md Part K). Weights/artifacts go to object storage (Phase 4).
- Small runtime > small everything: web/UI build is one stage, runtime is slim; training/serving differences noted for LLM images (AI-specific overload) — here the container is a server, keep it slim.
- `hadolint` in CI (linter) + `trivy`/`pip-audit` scanning (plan.md Part K).