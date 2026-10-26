"""G6 HTTP plumbing for the extra cloud STT lanes (mistral, xai, elevenlabs, deepinfra) and
OpenAI streaming transcription (`stream=true` → server-sent `transcript.text.*` events).

Unlike `voice.cloud.post_multipart`, fields are an ordered list of `(name, value)` pairs so a
provider's own repetition convention is kept (`context_bias` repeated for Mistral, `keyterms`
for ElevenLabs). Keys travel only as headers and never reach errors, logs or events.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx

from auoreo.core.settings_g6 import VoiceG6Settings
from auoreo.voice.audio import VoiceUnavailable
from auoreo.voice.cloud import TIMEOUT_S, _error

Fields = Iterable[tuple[str, object | None]]


def voice_g6(settings: Any) -> VoiceG6Settings:
    g6 = getattr(settings, "g6", None)
    return g6.voice if g6 is not None else VoiceG6Settings()


def _data(fields: Fields) -> dict[str, str | list[str]]:
    """Ordered pairs → httpx form data; a repeated name becomes a list (sent as repeated parts)."""
    out: dict[str, str | list[str]] = {}
    for k, v in fields:
        if v is None or v == "":
            continue
        prev = out.get(k)
        if prev is None:
            out[k] = str(v)
        else:
            out[k] = [*(prev if isinstance(prev, list) else [prev]), str(v)]
    return out


async def post_form(
    provider: str, url: str, headers: dict[str, str], wav: bytes, fields: Fields, settings: Any = None
) -> dict[str, Any]:
    """POST `file=audio.wav` + ordered form fields; returns the JSON body. With `settings`,
    the audio is shaped first (silence trim / AAC, `voice.upload`)."""
    from auoreo.voice.upload import prepare_upload

    fname, blob, mime = await prepare_upload(wav, settings)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(
                url,
                headers=headers,
                files=[("file", (fname, blob, mime))],
                data=_data(fields),
            )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"{provider} request failed: {type(exc).__name__}") from None
    if r.status_code >= 400:
        raise _error(provider, r)
    try:
        out: dict[str, Any] = r.json()
    except ValueError:
        raise VoiceUnavailable(f"{provider} returned a non-JSON body") from None
    return out


async def stream_sse(
    provider: str, url: str, headers: dict[str, str], wav: bytes, fields: Fields, settings: Any = None
) -> AsyncIterator[dict[str, Any]]:
    """POST multipart and yield each `data: {json}` event of a text/event-stream answer."""
    from auoreo.voice.upload import prepare_upload

    fname, blob, mime = await prepare_upload(wav, settings)
    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT_S) as client,
            client.stream(
                "POST",
                url,
                headers={**headers, "Accept": "text/event-stream"},
                files=[("file", (fname, blob, mime))],
                data=_data(fields),
            ) as r,
        ):
            if r.status_code >= 400:
                await r.aread()
                raise _error(provider, r)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"{provider} request failed: {type(exc).__name__}") from None


def words_to_segments(words: list[Any], text: str) -> list[dict[str, Any]]:
    """ElevenLabs-style word list → sentence-ish segments (split after . ! ?)."""
    segs: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if not isinstance(w, dict) or w.get("type", "word") != "word":
            continue
        cur.append(w)
        if str(w.get("text", "")).rstrip().endswith((".", "!", "?")):
            segs.append(_seg(cur))
            cur = []
    if cur:
        segs.append(_seg(cur))
    if not segs and text:
        segs.append({"start": 0.0, "end": 0.0, "text": text})
    return segs


def _seg(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start": float(words[0].get("start", 0) or 0),
        "end": float(words[-1].get("end", 0) or 0),
        "text": " ".join(str(w.get("text", "")).strip() for w in words).strip(),
    }
