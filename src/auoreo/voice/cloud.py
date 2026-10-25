"""Shared HTTP plumbing for cloud voice backends (OpenAI, Groq, ElevenLabs).

Keys come only from the secret store (env → keyring → `secrets.db`); they are sent as
headers and never appear in errors, logs or events. 30 s timeout, no SDK retries (spec §24.5).
"""

from __future__ import annotations

from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.core.secrets import SecretStore
from auoreo.voice.audio import AudioError, VoiceUnavailable

TIMEOUT_S = 30.0


def secret(settings: Any, name: str) -> str | None:
    return SecretStore(settings.home, getattr(settings, "keyring", "allow-file")).get(name)


def require_key(settings: Any, name: str) -> str:
    key = secret(settings, name)
    if not key:
        raise VoiceUnavailable(f"{name} is not set; run `auoreo secrets set {name}` or export it")
    return key


def key_status(settings: Any, name: str, what: str) -> BackendStatus:
    if secret(settings, name):
        return BackendStatus(True, f"{what} (key {name} present)")
    return BackendStatus(False, f"{name} is not set", remedy=f"auoreo secrets set {name}")


def _error(provider: str, r: httpx.Response) -> AudioError | VoiceUnavailable:
    try:
        body = r.json()
        err = body.get("error") or body.get("detail") or body
        msg = err.get("message") if isinstance(err, dict) else err
    except ValueError:
        msg = r.text
    cls = VoiceUnavailable if r.status_code in (401, 403, 429) or r.status_code >= 500 else AudioError
    return cls(f"{provider} request failed: HTTP {r.status_code}: {str(msg)[:300]}")


async def post_multipart(
    provider: str, url: str, key: str, wav: bytes, fields: dict[str, Any]
) -> dict[str, Any]:
    """POST `file=audio.wav` + form fields (lists become repeated `name[]` fields)."""
    data: dict[str, str | list[str]] = {}
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            data[f"{name}[]"] = [str(v) for v in value]
        else:
            data[name] = str(value)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data=data,
            )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"{provider} request failed: {type(exc).__name__}") from None
    if r.status_code >= 400:
        raise _error(provider, r)
    out: dict[str, Any] = r.json()
    return out


async def post_json_audio(
    provider: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    params: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """POST JSON, expect audio bytes. Returns `(bytes, content_type)`."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(url, headers=headers, json=body, params=params)
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"{provider} request failed: {type(exc).__name__}") from None
    if r.status_code >= 400:
        raise _error(provider, r)
    return r.content, r.headers.get("content-type", "application/octet-stream").split(";")[0]


def segments_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "start": float(s.get("start", 0)),
            "end": float(s.get("end", 0)),
            "text": str(s.get("text", "")).strip(),
        }
        for s in body.get("segments") or []
        if isinstance(s, dict)
    ]
