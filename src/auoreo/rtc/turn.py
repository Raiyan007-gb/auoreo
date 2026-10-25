"""Ephemeral TURN credentials for the aiortc P2P path (F5; spec §19.12, §26.8).

TURN REST API scheme, as implemented by coturn's `use-auth-secret` + `static-auth-secret`:

    username   = "<expiry unix seconds>:<user>"
    credential = base64(HMAC-SHA1(key=shared secret, msg=username))

The shared secret comes from `settings.turn_secret_ref` (`secret:<NAME>` in env / keyring /
secrets.db) and never leaves the server: callers get only the derived, time-limited pair.
Imports nothing heavy, so the API and doctor can use it without aiortc installed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from auoreo.core.secrets import SecretStore

DEFAULT_USER = "auoreo"


def turn_credentials(secret: str, user: str, ttl_s: int, *, now: float | None = None) -> tuple[str, str, int]:
    """`(username, credential, expires_at)` valid for `ttl_s` seconds from `now`."""
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    if ":" in user:
        raise ValueError("user must not contain ':'")
    expires = int(time.time() if now is None else now) + int(ttl_s)
    username = f"{expires}:{user}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode("ascii"), expires


def turn_secret(settings: Any, secrets: SecretStore | None = None) -> str | None:
    """Resolve `settings.turn_secret_ref`; None when unset or the secret is missing."""
    ref = getattr(settings, "turn_secret_ref", None)
    if not ref:
        return None
    store = secrets or SecretStore(settings.home, getattr(settings, "keyring", "allow-file"))
    return store.get(str(ref).removeprefix("secret:")) or None


def ephemeral_configured(settings: Any, secrets: SecretStore | None = None) -> bool:
    return bool(getattr(settings, "turn_urls", None)) and turn_secret(settings, secrets) is not None


def ice_servers(
    settings: Any,
    secrets: SecretStore | None = None,
    *,
    user: str = DEFAULT_USER,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """`(iceServers, expires_at)` in the browser `RTCIceServer` shape: the static
    `rtc_ice_servers` first, then one entry for `turn_urls` with fresh credentials."""
    servers: list[dict[str, Any]] = [{"urls": [u]} for u in settings.rtc_ice_servers]
    secret = turn_secret(settings, secrets)
    urls = list(getattr(settings, "turn_urls", []) or [])
    if not secret or not urls:
        return servers, None
    username, credential, expires = turn_credentials(secret, user, int(settings.turn_ttl_s), now=now)
    servers.append({"urls": urls, "username": username, "credential": credential})
    return servers, expires
