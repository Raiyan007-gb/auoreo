#!/usr/bin/env bash
# Hermetic test run: clean env, fixed hash seed, throwaway HOME (never touches ~/.auoreo).
set -euo pipefail
cd "$(dirname "$0")/.."
tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT
exec env -i PATH="$PATH" HOME="$tmp_home" LANG="${LANG:-C.UTF-8}" PYTHONHASHSEED=0 \
  UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}" \
  ${SSL_CERT_FILE:+SSL_CERT_FILE="$SSL_CERT_FILE"} \
  uv run --frozen pytest "$@"
