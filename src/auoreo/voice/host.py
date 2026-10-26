"""`auoreo voice-host`: the out-of-process voice media sidecar (spec §13 / §24.5, F6).

Run as `python -m auoreo.voice.host` (optionally from another venv, `settings.voice_host_python`).
It loads the STT/TTS/VAD backends named in `settings.voice_host_backends` - heavy models, GPU
libraries and GPL/NC engines (piper, openwakeword) live only in this process - and serves
JSON-RPC 2.0, one object per line on stdio (the `plugins.protocol.RpcPeer` framing):

    check {}                                   → {pid, backends: {kind: {name, ok, detail, remedy}}}
    transcribe {wav_b64, language, keyterms}   → Transcript dict
    synthesize {text, voice, speed}            → {audio_b64, media_type, sample_rate}
    vad {wav_b64}                              → {segments: [[start_s, end_s], ...]}
    wake {wav_b64}                             → {hit: {phrase, start, score} | null}   (I8)
    shutdown {}                                → {ok: true}, then exit

Binary payloads are base64 and capped at 25 MB decoded, both directions. A backend `ToolError`
is answered with code -32001 (its message is user-facing). Real stdout is reserved for the
protocol: fd 1 is re-pointed at stderr so a chatty library cannot corrupt the stream.
`AUOREO_VOICE_HOST_DEBUG=1` adds `debug.crash|sleep|env` for the test-suite.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import IO, Any

MAX_PAYLOAD_BYTES = 25 * 1024 * 1024
MAX_B64_CHARS = (MAX_PAYLOAD_BYTES + 2) // 3 * 4
SETTINGS_ENV = "AUOREO_VOICE_HOST_SETTINGS"
DEBUG_ENV = "AUOREO_VOICE_HOST_DEBUG"
KINDS = ("stt", "tts", "vad", "wake")
TOOL_ERROR, BAD_PARAMS, NO_METHOD = -32001, -32602, -32601


class HostError(Exception):
    def __init__(self, message: str, code: int = TOOL_ERROR) -> None:
        super().__init__(message)
        self.code = code


def decode_payload(value: Any, field: str = "wav_b64") -> bytes:
    if not isinstance(value, str):
        raise HostError(f"{field} must be a base64 string", BAD_PARAMS)
    if len(value) > MAX_B64_CHARS:
        raise HostError(f"{field} exceeds the {MAX_PAYLOAD_BYTES} byte (25 MB) payload cap", BAD_PARAMS)
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HostError(f"{field} is not valid base64: {exc}", BAD_PARAMS) from exc


def encode_payload(data: bytes) -> str:
    if len(data) > MAX_PAYLOAD_BYTES:
        raise HostError(f"result is {len(data)} bytes; the payload cap is {MAX_PAYLOAD_BYTES} (25 MB)")
    return base64.b64encode(data).decode()


class VoiceHostServer:
    """Owns the inner backends; one instance per sidecar process."""

    def __init__(self, settings: Any, names: dict[str, str]) -> None:
        self.settings = settings
        self.names = names
        self._inst: dict[str, Any] = {}
        self._harness: Any = None
        self.stop = asyncio.Event()
        self.methods: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "check": self.check,
            "transcribe": self.transcribe,
            "synthesize": self.synthesize,
            "vad": self.vad,
            "wake": self.wake,
            "shutdown": self.shutdown,
        }
        if os.environ.get(DEBUG_ENV) == "1":
            self.methods.update({"debug.crash": _crash, "debug.sleep": _sleep, "debug.env": _env})

    def _bdef(self, kind: str) -> Any:
        if self._harness is None:
            from auoreo.core.paths import bundled_root
            from auoreo.harness.discovery import default_roots
            from auoreo.harness.registry import Harness

            st = self.settings
            self._harness = Harness(default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots))
        name = self.names.get(kind) or "fake"
        if name == "sidecar":
            raise HostError(f"voice_host_backends.{kind} cannot be 'sidecar' (it would recurse)")
        bdef = self._harness.select_backend(kind, name)
        if bdef is None:
            raise HostError(f"no '{kind}' backend named {name!r}")
        return bdef

    def backend(self, kind: str) -> Any:
        if kind not in self._inst:
            self._inst[kind] = self._bdef(kind).load()(self.settings)
        return self._inst[kind]

    async def check(self, _p: dict[str, Any]) -> dict[str, Any]:
        rows: dict[str, Any] = {}
        for kind in KINDS:
            row: dict[str, Any] = {"name": self.names.get(kind), "ok": False, "detail": "", "remedy": ""}
            try:
                st = await asyncio.wait_for(self.backend(kind).check(), 10)
                row.update(ok=bool(st.ok), detail=st.detail, remedy=st.remedy)
            except Exception as exc:  # report, never die on a broken inner backend
                row["detail"] = f"{type(exc).__name__}: {exc}"[:300]
            rows[kind] = row
        return {"pid": os.getpid(), "python": sys.executable, "backends": rows}

    async def transcribe(self, p: dict[str, Any]) -> dict[str, Any]:
        wav = decode_payload(p.get("wav_b64"))
        t = await self.backend("stt").transcribe(
            wav, language=p.get("language"), keyterms=list(p.get("keyterms") or [])
        )
        result: dict[str, Any] = t.to_dict()
        return result

    async def synthesize(self, p: dict[str, Any]) -> dict[str, Any]:
        text = p.get("text")
        if not isinstance(text, str):
            raise HostError("text must be a string", BAD_PARAMS)
        audio = await self.backend("tts").synthesize(
            text, voice=p.get("voice"), speed=float(p.get("speed") or 1.0)
        )
        return {
            "audio_b64": encode_payload(audio.data),
            "media_type": audio.media_type,
            "sample_rate": audio.sample_rate,
            "chunk_limit": getattr(self.backend("tts"), "chunk_limit", 2000),
        }

    async def vad(self, p: dict[str, Any]) -> dict[str, Any]:
        segs = await self.backend("vad").speech_segments(decode_payload(p.get("wav_b64")))
        return {"segments": [[float(a), float(b)] for a, b in segs]}

    async def wake(self, p: dict[str, Any]) -> dict[str, Any]:
        hit = await self.backend("wake").detect(decode_payload(p.get("wav_b64")))
        if hit is None:
            return {"hit": None}
        return {"hit": {"phrase": hit.phrase, "start": float(hit.start), "score": float(hit.score)}}

    async def shutdown(self, _p: dict[str, Any]) -> dict[str, Any]:
        self.stop.set()
        return {"ok": True}

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        rid, method = msg.get("id"), str(msg.get("method", ""))
        fn = self.methods.get(method)
        try:
            if fn is None:
                raise HostError(f"unknown method {method!r}", NO_METHOD)
            reply: dict[str, Any] = {"result": await fn(msg.get("params") or {})}
        except HostError as exc:
            reply = {"error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # a backend ToolError (or bug) answers, never kills the host
            reply = {"error": {"code": TOOL_ERROR, "message": str(exc) or type(exc).__name__}}
        return None if rid is None else {"jsonrpc": "2.0", "id": rid, **reply}


async def _crash(_p: dict[str, Any]) -> None:
    os._exit(3)


async def _sleep(p: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(float(p.get("s", 1)))
    return {"slept": p.get("s", 1)}


async def _env(_p: dict[str, Any]) -> dict[str, Any]:
    return {"keys": sorted(os.environ)}


def host_names(st: Any) -> dict[str, str]:
    """Inner backend per kind: `voice_host_backends` (stt/tts/vad) + `i8.wake.host_backend`."""
    names: dict[str, str] = dict(st.voice_host_backends.model_dump())
    i8 = getattr(st, "i8", None)
    names["wake"] = str(i8.wake.host_backend) if i8 is not None else "fake"
    return names


def _settings() -> Any:
    from auoreo.core.settings import Settings

    raw = os.environ.get(SETTINGS_ENV)
    return Settings(**json.loads(raw)) if raw else Settings()


async def serve(proto_out: IO[bytes]) -> None:
    st = _settings()
    server = VoiceHostServer(st, host_names(st))
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_B64_CHARS + 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    lock = asyncio.Lock()
    tasks: set[asyncio.Task[None]] = set()

    async def run(msg: dict[str, Any]) -> None:
        reply = await server.handle(msg)
        if reply is not None:
            data = (json.dumps(reply) + "\n").encode()
            async with lock:
                proto_out.write(data)
                proto_out.flush()

    async def read() -> None:
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                task = asyncio.create_task(run(msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        server.stop.set()  # stdin closed: the control plane went away

    reading = asyncio.create_task(read())
    await server.stop.wait()
    await asyncio.gather(*tasks, return_exceptions=True)
    reading.cancel()


def main() -> None:
    proto_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # library prints go to stderr, never into the protocol stream
    sys.stdout = sys.stderr
    try:
        asyncio.run(serve(proto_out))
    except (ValueError, asyncio.LimitOverrunError) as exc:  # an oversized line: refuse loudly
        print(f"voice-host: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        proto_out.flush()


if __name__ == "__main__":
    main()
