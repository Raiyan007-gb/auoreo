#!/usr/bin/env python3
"""Example AUOREO hook (`hooks/lint_python.yaml`): lint edited Python files with ruff.

Reads the hook payload (JSON) from stdin and prints the decision as JSON on stdout.
PostToolUse is informational, so it always answers `allow`, with ruff's findings in
`message` (PostToolUse replies are informational; the engine records `hook.ran`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    path = (payload.get("arguments") or {}).get("path", "")
    if not isinstance(path, str) or not path.endswith(".py") or payload.get("is_error"):
        print(json.dumps({"decision": "allow"}))
        return 0
    ruff = shutil.which("ruff")
    if ruff is None:
        print(json.dumps({"decision": "allow", "message": "ruff not found on PATH"}))
        return 0
    proc = subprocess.run([ruff, "check", "--quiet", path], capture_output=True, text=True, timeout=15)
    message = (proc.stdout or proc.stderr).strip()[:2000] or "ruff: clean"
    print(json.dumps({"decision": "allow", "message": message}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
