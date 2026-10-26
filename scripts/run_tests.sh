#!/usr/bin/env bash
# Hermetic test run (spec §20.4): clean env, fixed hash seed, throwaway HOME (never touches
# ~/.auoreo), venv probe.
#
#   scripts/run_tests.sh [pytest args…]                 one pytest process (default)
#   scripts/run_tests.sh --per-file [paths…] [-- args]  one subprocess per test file (isolates
#                                                        module-level state; slower). Paths
#                                                        default to tests/; prints a summary and
#                                                        exits 1 if any file failed.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! uv run --frozen python -c "import auoreo, pytest" >/dev/null 2>&1; then
  echo "run_tests.sh: the project venv is not synced; run: uv sync --all-extras --no-extra rerank-local" >&2
  exit 2
fi

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT

hermetic() {
  env -i PATH="$PATH" HOME="$tmp_home" LANG="${LANG:-C.UTF-8}" PYTHONHASHSEED=0 \
    UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}" \
    ${SSL_CERT_FILE:+SSL_CERT_FILE="$SSL_CERT_FILE"} \
    ${CI:+CI="$CI"} \
    uv run --frozen pytest "$@"
}

if [[ "${1:-}" != "--per-file" ]]; then
  hermetic "$@"
  exit $?
fi

shift
paths=()
extra=()
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--" ]]; then
    shift
    extra=("$@")
    break
  fi
  paths+=("$1")
  shift
done
[[ ${#paths[@]} -gt 0 ]] || paths=(tests)

mapfile -t files < <(find "${paths[@]}" -name 'test_*.py' -type f | sort)
failed=()
for f in "${files[@]}"; do
  if hermetic -q -p no:cacheprovider "${extra[@]}" "$f" >"$tmp_home/last.log" 2>&1; then
    printf 'ok    %s\n' "$f"
  else
    code=$?
    if [[ $code -eq 5 ]]; then  # no tests collected (e.g. all skipped by markers)
      printf 'empty %s\n' "$f"
      continue
    fi
    printf 'FAIL  %s (exit %d)\n' "$f" "$code"
    tail -n 30 "$tmp_home/last.log" | sed 's/^/      /'
    failed+=("$f")
  fi
done
printf '\n%d file(s), %d failed\n' "${#files[@]}" "${#failed[@]}"
[[ ${#failed[@]} -eq 0 ]]
