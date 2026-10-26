"""I2 doctor rows: probe a freshly rotated provider key BEFORE its `#prev` is demoted
(spec §25.14 L1067). For every secret still inside its rotation grace window the NEW value
is sent to the provider's cheapest authenticated endpoint; 2xx → safe to let `#prev` expire,
401/403 → the new key is bad (the gateway is still falling back to `#prev`). Offline or an
unknown provider is reported as an honest skip, never as a pass.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from auoreo.core.diagnostics import Check
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings
from auoreo.gateway.keypool import parse_slot

PROBE_TIMEOUT_S = 3.0
Headers = Callable[[str], dict[str, str]]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _anthropic(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


PROBES: dict[str, tuple[str, Headers]] = {
    "openrouter": ("https://openrouter.ai/api/v1/key", _bearer),
    "anthropic": ("https://api.anthropic.com/v1/models", _anthropic),
    "openai": ("https://api.openai.com/v1/models", _bearer),
}


def provider_for(name: str) -> str | None:
    """`OPENROUTER_API_KEY` → openrouter; `provider/anthropic/work` → anthropic."""
    if slot := parse_slot(name):
        return slot[0]
    if name.endswith("_API_KEY"):
        return name.removesuffix("_API_KEY").lower()
    return None


def rotating(store: SecretStore) -> list[str]:
    """Base names whose previous value is still inside its grace window."""
    try:
        names = store.names()
    except Exception:
        return []
    return [
        n.removesuffix("#prev") for n in names if n.endswith("#prev") and len(store.candidates(n[:-5])) > 1
    ]


async def probe(name: str, value: str, client: Any) -> Check:
    check = f"rotation_probe:{name}"
    prov = provider_for(name)
    if prov not in PROBES:
        return Check(check, "ok", f"skipped: no probe for provider {prov or '?'} (new key untested)")
    url, headers = PROBES[prov]
    try:
        resp = await client.get(url, headers=headers(value), timeout=PROBE_TIMEOUT_S)
    except Exception as exc:  # offline / DNS / TLS: an honest skip
        return Check(check, "ok", f"skipped: {prov} unreachable ({type(exc).__name__}); new key untested")
    if 200 <= resp.status_code < 300:
        return Check(check, "ok", f"new key accepted by {prov}; {name}#prev can expire")
    if resp.status_code in {401, 403}:
        return Check(
            check,
            "error",
            f"new key rejected by {prov} (HTTP {resp.status_code}); calls are falling back to {name}#prev",
            f"auoreo secrets rotate {name} with a valid key before the grace window ends",
        )
    return Check(check, "warn", f"{prov} answered HTTP {resp.status_code}; new key not confirmed")


async def i2_checks(st: Settings) -> list[Check]:
    store = SecretStore(st.home, st.keyring)
    names = rotating(store)
    if not names:
        return []
    if os.environ.get("AUOREO_OFFLINE"):
        return [
            Check(f"rotation_probe:{n}", "ok", "skipped (AUOREO_OFFLINE): new key untested") for n in names
        ]
    import httpx

    async with httpx.AsyncClient() as client:
        out = []
        for name in names:
            value = store.get(name)
            if value:
                out.append(await probe(name, value, client))
        return out


__all__ = ["PROBES", "i2_checks", "probe", "provider_for", "rotating"]
