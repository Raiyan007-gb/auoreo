#!/usr/bin/env bash
# F7 spike: the recommended topology end to end - uvicorn (auoreo serve, HTTP/1.1) behind
# deploy/caddy/Caddyfile (h1/h2/h3) - probed over HTTP/3 by scripts/spikes/http3_probe.py.
#
#   CADDY=/path/to/caddy bash scripts/spikes/http3_caddy_e2e.sh
#
# Unprivileged ports: uvicorn 14096, Caddy https 18443 (TCP+UDP), http 18080. Caddy's
# internal CA issues the `localhost` certificate (the probe does not verify it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CADDY="${CADDY:-caddy}"
TMP="$(mktemp -d)"
export AUOREO_HOME="$TMP/home" AUOREO_GATEWAY=fake XDG_DATA_HOME="$TMP/xdg" XDG_CONFIG_HOME="$TMP/xdg"
unset AUOREO_SERVER_PASSWORD || true

cleanup() {
  kill "${CADDY_PID:-}" "${AUOREO_PID:-}" 2>/dev/null || true
  pkill -f "auoreo serve --host 127.0.0.1 --port 14096" 2>/dev/null || true  # uv run's child
}
trap cleanup EXIT

(cd "$ROOT" && uv run auoreo serve --host 127.0.0.1 --port 14096 >"$TMP/auoreo.log" 2>&1) &
AUOREO_PID=$!
AUOREO_DOMAIN=localhost:18443 CADDY_HTTP_PORT=18080 CADDY_HTTPS_PORT=18443 \
  AUOREO_UPSTREAM=127.0.0.1:14096 "$CADDY" run --config "$ROOT/deploy/caddy/Caddyfile" \
  --adapter caddyfile >"$TMP/caddy.log" 2>&1 &
CADDY_PID=$!

for _ in $(seq 1 60); do
  curl -sk --max-time 1 https://localhost:18443/api/health >/dev/null 2>&1 && break
  sleep 0.5
done
cd "$ROOT"
uv run --with 'hypercorn[h3]' python scripts/spikes/http3_probe.py --target 18443 --requests "${REQUESTS:-200}"
