---
name: docker
description: Package the platform as containers — multi-stage Dockerfile best practices, non-root, healthchecks, multi-arch, GHCR tags, Docker Compose for local dev. Use when working in containers/ in this repo.
---

# Docker — containers/ (GHCR + Compose)

Delivers the full "server + web UI on one port" experience: `docker run -p 4096:4096 ghcr.io/<org>/agent-platform:<tag>` (plan.md Part J channel). Multi-arch builds run in CI (`containers.yml`).

## Multi-stage Dockerfile (containers/Dockerfile)

Keep runtime image small and non-root (open-code + plan.md Part K security):

```dockerfile
# syntax=docker/dockerfile:1
# ⚠ Dockerfile comments must be on their own line — a trailing `# ...` after FROM/COPY is parsed as arguments.

# ---- build stage: build web UI one time ----
FROM node:22-alpine AS web
WORKDIR /w
COPY web/app/package.json web/app/package-lock.json ./
RUN npm ci
COPY web/app/ ./
RUN npm run build                     # → dist/ (shell comment inside RUN is fine)

# (optional) SDK/launcher deps
FROM node:22-alpine AS tsdeps
# ...

# ---- python deps stage (uv official Docker pattern) ----
# ⚠ pin the version tag or @sha256 digest (moving tags = unreproducible)
FROM ghcr.io/astral-sh/uv:0.13.5-python3.12-trixie-slim AS py
# UV_PYTHON_DOWNLOADS=0 → use the image's system Python (same /usr/local path as the runtime image)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# 1st sync: deps only (cache mount + bind-mount uv.lock + --no-install-project per uv guide)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project --no-editable
COPY . /app
# 2nd sync: install THIS project into the venv (--no-editable → runtime needs only /app/.venv)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# ---- runtime (venv only; no project source) — same Python minor/base as the py stage ----
FROM python:3.12-slim-trixie AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
# venv WITH the `agent` console script installed
COPY --from=py /app/.venv /app/.venv
COPY --from=web /w/dist /app/web/app/dist
# non-root
RUN useradd --uid 10001 appuser
USER appuser
EXPOSE 4096
# real endpoint; --start-period gives the server time to boot before failures count
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD /app/.venv/bin/python -m agent_cli health || exit 1
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

- Tag with version: `ghcr.io/<org>/agent-platform:v1.2.3`, plus `latest` (generate via `docker/metadata-action@v6` `type=semver,pattern={{raw}}` keeps the `v` prefix; `latest` is auto-added for semver tags via `flavor`); use `docker/build-push-action@v7` with platforms `linux/amd64,linux/arm64` (`docker/setup-qemu-action@v4` + `docker/setup-buildx-action@v4`) — plan.md Part J.
- `provenance: true` (SLSA/attestations) on push — without it the action already defaults to `mode=max` on public repos but only `mode=min,inline-only` on private ones; add `sbom: true` for an SBOM attestation. Registry login via `docker/login-action@v4` + `GITHUB_TOKEN` (`packages: write`) for GHCR.
- Pin base images by digest where practical; enable Dependabot for base-image bumps.

## Best practices (Context7: `/docker/docs`, `/astral-sh/uv`, `/docker/build-push-action`)

- Layer caching: `COPY` dependency manifests + `install/ci` before copying source (invalidates on code change, not README).
- `.dockerignore` must list `.venv` (and `node_modules`, `dist`) — `COPY . /app` would otherwise clobber the stage's venv with a host one (uv guide).
- Don't bake **model weights or API keys** into the image; read keys from env at runtime (plan.md Part K). Weights/artifacts go to object storage (Phase 4).
- Small runtime > small everything: web/UI build is one stage, runtime is slim; training/serving differences noted for LLM images (AI-specific overload) — here the container is a server, keep it slim.
- `hadolint` in CI (linter) + `trivy`/`pip-audit` scanning (plan.md Part K).