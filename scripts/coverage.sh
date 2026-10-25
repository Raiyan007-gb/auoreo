#!/usr/bin/env bash
# Coverage gate (spec §20.4): the full suite under pytest-cov, a global floor
# (--cov-fail-under) and a per-file floor (scripts/check_coverage.py). Kept out of the default
# pytest addopts so a plain `pytest` stays fast; CI runs this as the `coverage` job.
#
#   scripts/coverage.sh                 # whole suite
#   scripts/coverage.sh tests/unit -x   # any extra pytest args (floors still apply to what ran)
#
# Floors only go up: raise COV_FLOOR / COV_FILE_FLOOR when coverage grows.
set -euo pipefail
cd "$(dirname "$0")/.."
# Measured 2026-10-25 (G5c): 90.4% total over 281 files; floors leave ~2 points of CI slack.
COV_FLOOR="${COV_FLOOR:-88}"
COV_FILE_FLOOR="${COV_FILE_FLOOR:-20}"
mkdir -p .artifacts
uv run --frozen pytest -p no:warnings \
  --cov --cov-report=term:skip-covered --cov-report=json:.artifacts/coverage.json \
  --cov-fail-under="$COV_FLOOR" --junitxml=.artifacts/junit.xml "$@"
uv run --frozen python scripts/check_coverage.py .artifacts/coverage.json --per-file "$COV_FILE_FLOOR" \
  --exempt '*/auoreo/cli/main.py' --exempt '*/auoreo/plugins/rt/*'  # entry point; plugin subprocess code
