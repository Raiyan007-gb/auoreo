"""Dump the OpenAPI contract to web/sdk/openapi.json, or `--check` that it is current."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from auoreo.api.app import create_app
from auoreo.core.settings import Settings

TARGET = Path(__file__).resolve().parents[1] / "web" / "sdk" / "openapi.json"


def main() -> int:
    spec = create_app(Settings(gateway="fake")).openapi()
    text = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        current = TARGET.read_text() if TARGET.is_file() else ""
        if current != text:
            print(f"{TARGET} is stale; run: uv run python scripts/check_openapi.py", file=sys.stderr)
            return 1
        print("openapi.json is up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
