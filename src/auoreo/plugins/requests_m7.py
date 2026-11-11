"""M7 plugin → host requests (docs/designs/m7-agents-bots.md, Wave 2), kept out of `host.py`
and `requests.py` for the 500-line cap. Every request is checked here, host side:

- `voice.transcribe {chat_id, mime, data_b64}` needs `consumes: [voice]`, the
  `m7.agents.voice_inbound` flag, a paired chat, the size cap and the per-plugin daily quota
  (`<home>/plugins/<name>/voice_quota.json`, UTC day). The audio is decoded and measured
  BEFORE the STT call, so a plugin cannot under-declare a note's length; the seconds are
  charged before transcription (fail closed). Audit rows `plugin.voice` carry sizes and seconds,
  never the transcript.
- `state.get` / `state.set {state}` need `consumes: [state]`: one JSON object of at most
  16 KiB in `<home>/plugins/<name>/state.json` (a cursor and counters survive a restart).

`init_extra` / `release` give a plugin that consumes `net:proxy` a host-minted egress-proxy
token in `initialize` (never in its environment) and revoke it when the plugin stops.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from auoreo.plugins.connectors import PairingStore, _read_json, _write_json
from auoreo.plugins.protocol import RpcError

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.plugins.host import PluginHost, PluginState

STATE_MAX = 16 * 1024
REFUSED = -32003
"""A request the host understood and refused (flag off, unpaired, over a cap)."""
_QUOTA_LOCKS: dict[str, asyncio.Lock] = {}
_PROXY_TOKENS: dict[str, str] = {}
"""plugin name → live egress-proxy token (revoked by `release`)."""
GRANTABLE = frozenset({"voice", "state", "consent:post", "net:proxy"})


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def _deny(host: PluginHost, st: PluginState, action: str, reason: str, detail: dict[str, Any]) -> None:
    await host.s.audit.record(
        actor=f"plugin:{st.name}", action=action, decision="deny", reason=reason, detail=detail
    )


# ------------------------------------------------------------------ voice
async def voice_transcribe(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    cfg = host.s.settings.m7.agents
    chat_id = str(p.get("chat_id", ""))[:128]
    base = {"chat_id": chat_id[:64]}
    if "voice" not in st.manifest.consumes:
        await _deny(host, st, "plugin.voice", "not declared in consumes", base)
        raise RpcError("voice.transcribe needs `consumes: [voice]` in plugin.yaml", code=-32001)
    if not cfg.voice_inbound:
        await _deny(host, st, "plugin.voice", "m7.agents.voice_inbound is off", base)
        raise RpcError("voice notes are disabled on this host", code=REFUSED)
    if not chat_id or chat_id not in PairingStore(host.home, st.name).allowlist(
        [str(c) for c in st.config.get("allowlist") or []]
    ):
        await _deny(host, st, "plugin.voice", "chat is not paired", base)
        raise RpcError("this chat is not paired", code=REFUSED)
    raw = str(p.get("data_b64", ""))
    if len(raw) > 4 * math.ceil(cfg.voice_max_bytes / 3) + 4:
        await _deny(host, st, "plugin.voice", "too large", {**base, "b64_chars": len(raw)})
        raise RpcError(f"voice note is larger than {cfg.voice_max_bytes} bytes", code=REFUSED)
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RpcError("data_b64 is not valid base64", code=-32602) from exc
    if not data or len(data) > cfg.voice_max_bytes:
        await _deny(host, st, "plugin.voice", "size", {**base, "bytes": len(data)})
        raise RpcError(f"voice note must be 1..{cfg.voice_max_bytes} bytes", code=REFUSED)
    async with _QUOTA_LOCKS.setdefault(st.name, asyncio.Lock()):
        return await _transcribe(host, st, data, str(p.get("mime") or "") or None, base)


async def _transcribe(
    host: PluginHost, st: PluginState, data: bytes, mime: str | None, base: dict[str, Any]
) -> Any:
    from auoreo.tools.context import ToolError
    from auoreo.voice import voice_for
    from auoreo.voice.audio import NoSpeech, duration_s, normalize

    cfg = host.s.settings.m7.agents
    path = host.home / "plugins" / st.name / "voice_quota.json"
    quota = _read_json(path)
    used = float(quota.get("seconds", 0)) if quota.get("day") == today() else 0.0
    if used >= cfg.voice_daily_seconds:
        await _deny(host, st, "plugin.voice", "daily quota", {**base, "used_s": used})
        raise RpcError("the daily voice quota is used up", code=REFUSED)
    try:
        wav = await normalize(data, mime)
    except (ToolError, OSError) as exc:
        raise RpcError(f"cannot decode the voice note ({type(exc).__name__})", code=REFUSED) from exc
    seconds = round(duration_s(wav), 3)
    detail = {**base, "bytes": len(data), "seconds": seconds, "used_s": used}
    if seconds > cfg.voice_max_seconds:
        await _deny(host, st, "plugin.voice", "too long", detail)
        raise RpcError(f"voice note is {seconds:.0f} s; the limit is {cfg.voice_max_seconds} s", code=REFUSED)
    if used + seconds > cfg.voice_daily_seconds:
        await _deny(host, st, "plugin.voice", "daily quota", detail)
        raise RpcError("the daily voice quota is used up", code=REFUSED)
    _write_json(path, {"day": today(), "seconds": round(used + seconds, 3)})  # charged before STT
    try:  # VAD gate + STT + filter, without the `voice.transcribed` event (it carries the text)
        voice = voice_for(host.s)
        if not await voice.vad.speech_segments(wav):
            raise NoSpeech("no speech detected")
        t = await voice.transcribe_wav(wav)
        if not voice.judge(t).keep or not t.text.strip():
            raise NoSpeech("no speech detected")
    except NoSpeech as exc:
        await _deny(host, st, "plugin.voice", "no speech", detail)
        raise RpcError("no speech detected", code=REFUSED) from exc
    except ToolError as exc:
        await _deny(host, st, "plugin.voice", "stt unavailable", detail)
        raise RpcError(f"speech-to-text is unavailable ({type(exc).__name__})", code=REFUSED) from exc
    await host.s.audit.record(
        actor=f"plugin:{st.name}", action="plugin.voice", decision="allow", detail=detail
    )
    return {"text": t.text.strip(), "duration_s": seconds}


# ------------------------------------------------------------------ state
def _state_path(host: PluginHost, st: PluginState) -> Any:
    return host.home / "plugins" / st.name / "state.json"


def _need_state(st: PluginState) -> None:
    if "state" not in st.manifest.consumes:
        raise RpcError("state.get/state.set need `consumes: [state]` in plugin.yaml", code=-32001)


async def state_get(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    _need_state(st)
    return {"state": _read_json(_state_path(host, st))}


async def state_set(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    _need_state(st)
    state = p.get("state")
    if not isinstance(state, dict):
        raise RpcError("state must be a JSON object", code=-32602)
    if len(json.dumps(state, default=str)) > STATE_MAX:
        raise RpcError(f"state exceeds {STATE_MAX} bytes", code=-32602)
    _write_json(_state_path(host, st), json.loads(json.dumps(state, default=str)))
    return {"ok": True}


M7_REQUESTS = {
    "voice.transcribe": voice_transcribe,
    "state.get": state_get,
    "state.set": state_set,
}


# ------------------------------------------------------------------ init
def granted(consumes: list[str]) -> list[str]:
    """The M7 entries of `consumes` echoed in `initialize.consumes_granted`."""
    return [c for c in consumes if c in GRANTABLE]


async def init_extra(services: Services, st: PluginState) -> dict[str, Any]:
    """Extra `initialize` params: `{proxy}` for a plugin that consumes `net:proxy`, when the
    egress proxy runs. The token is minted per plugin start and never logged."""
    if "net:proxy" not in st.manifest.consumes:
        return {}
    from auoreo.tools.egress import ensure, session_proxy

    release(st.name)
    await ensure(services)
    routed = session_proxy(f"plugin:{st.name}")
    if routed is None:
        return {}
    p, token = routed
    _PROXY_TOKENS[st.name] = token
    return {"proxy": p["server"].replace("http://", f"http://{p['username']}:{p['password']}@", 1)}


def release(name: str) -> None:
    """Revoke the plugin's proxy token (stop, crash, restart)."""
    token = _PROXY_TOKENS.pop(name, None)
    if token is not None:
        from auoreo.tools.egress import revoke_session

        revoke_session(token)
