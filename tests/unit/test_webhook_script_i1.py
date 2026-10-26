"""I1 §15 L660: a webhook `script:` transform's stdout is redacted and capped at 16 KiB."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.automation.webhook_extras import STDOUT_CAP, ScriptError, run_script
from auoreo.core.services import Services
from auoreo.harness.manifests import WebhookManifest

KEY = "sk-proj-" + "Z9y8X7w6V5u4T3s2R1q0" * 2


def _manifest(script: str) -> WebhookManifest:
    return WebhookManifest(name="r", description="d", secret="s", prompt="p", script=script)


def _write(services: Services, name: str, body: str) -> None:
    d = Path(services.settings.home) / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


async def test_script_stdout_is_redacted(services: Services) -> None:
    _write(services, "leak.py", f"import json\nprint(json.dumps({{'note': 'key {KEY}', 'n': 1}}))\n")
    out = await run_script(services, _manifest("leak.py"), {})
    assert KEY not in str(out) and out["n"] == 1 and out["note"].startswith("key ")


async def test_script_stdout_is_capped_at_16k(services: Services) -> None:
    assert STDOUT_CAP == 16 * 1024
    _write(services, "big.py", "import json\nprint(json.dumps({'x': 'a' * 20000}))\n")
    with pytest.raises(ScriptError, match="more than 16384 bytes"):
        await run_script(services, _manifest("big.py"), {})
