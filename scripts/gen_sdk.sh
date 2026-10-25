#!/usr/bin/env bash
# Regenerate the Python SDK (sdk/python/auoreo_client) from web/sdk/openapi.json.
# Never hand-edit the generated package; the hand-written layer is sdk/python/auoreo_sdk_extras.
# `--meta none` writes just the package (no pyproject scaffold); `--overwrite` replaces it,
# so the output path is the package dir, not sdk/python (which also holds extras + README).
set -euo pipefail
cd "$(dirname "$0")/.."
OPC_VERSION="${OPC_VERSION:-0.29.1}"
uvx "openapi-python-client@${OPC_VERSION}" generate \
  --path web/sdk/openapi.json \
  --output-path sdk/python/auoreo_client \
  --config sdk/python/openapi-python-client.yaml \
  --overwrite \
  --meta none
echo "generated sdk/python/auoreo_client"
