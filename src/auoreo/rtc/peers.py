"""WebRTC peers (spec §2.1 "Full-duplex voice over WebRTC", M4e): signaling over the one
FastAPI port, media and the `auoreo-events` data channel over aiortc.

`RtcManager(services)` keeps `{rtc_id: Peer}`. A `Peer` is one `RTCPeerConnection`, the
client-created `auoreo-events` channel (see `auoreo.rtc.events`) and an optional inbound
audio track. aiortc is imported lazily so the core install works without the `rtc` extra.

Limits: SDP ≤ 64 KiB (`SdpTooLarge` → 413), data-channel messages ≤ 128 KiB, at most
`Settings.rtc_max_peers` peers (`RtcLimitError` → 429). ICE servers come from
`Settings.rtc_ice_servers` (empty = host candidates only, enough for loopback/LAN).
SDP is never logged or audited: log lines carry `[REDACTED sdp N bytes]`.

Audio: each inbound audio track is decoded and resampled to 16 kHz mono int16; its RMS is
emitted as `rtc.audio_level{rtc_id, session_id, rms, speaking}` on the `server` aggregate
at most once per `Settings.rtc_audio_level_interval_s`.

STT hook: set `RtcManager.on_audio_frames` to a callable
`(rtc_id: str, session_id: str | None, pcm: numpy.ndarray[int16], sample_rate: int)`
(sync or async). It receives every resampled chunk (~20 ms) of every peer's audio; it is
the entry point for the live transcription bridge. Exceptions are logged and swallowed.

Speaking (F1): when voice is enabled, `Settings.voice_speak_over_rtc` is true and the offer did
not say `speak: false`, an outbound audio track (`auoreo.rtc.outbound`) is attached to the
offer's audio transceiver before the answer is created (the client must offer audio,
`sendrecv` or `recvonly`). `Peer.say(pcm, rate)` queues speech, `Peer.stop_speaking()` drops
it at once and emits `voice.speech_cancelled`. Each speaking peer has an `EchoGuard`.

Teleop input (F1): `input: true` offers (only with `Settings.teleop_input_enabled`, else
`PermissionError` → 403) accept a client-created `auoreo-input` channel; see
`auoreo.rtc.teleop`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from auoreo.rtc.audio import SAMPLE_RATE, FramesHook, LevelMeter, consume_audio, drain_track
from auoreo.rtc.echo import EchoGuard
from auoreo.rtc.events import CHANNEL_LABEL, EventChannel
from auoreo.rtc.outbound import SpeechQueue, outbound_track
from auoreo.rtc.teleop import INPUT_LABEL, TeleopChannel
from auoreo.rtc.turn import ice_servers

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

MAX_SDP_BYTES = 64 * 1024
INSTALL_HINT = "install auoreo[rtc]"


class RtcUnavailable(RuntimeError):
    """aiortc is not installed (the `rtc` extra)."""


class RtcLimitError(RuntimeError):
    """`Settings.rtc_max_peers` reached."""


class SdpTooLarge(ValueError):
    """Offer SDP over `MAX_SDP_BYTES`."""


def redact_sdp(sdp: str) -> str:
    return f"[REDACTED sdp {len(sdp.encode())} bytes]"


def aiortc_available() -> bool:
    try:
        import aiortc  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(eq=False)
class Peer:
    rtc_id: str
    session_id: str | None
    pc: Any  # aiortc.RTCPeerConnection
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    events: EventChannel | None = None
    media_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    has_audio: bool = False
    speech: SpeechQueue | None = None
    """Outbound speech queue; None when this peer does not speak (opt-out or no audio m-line)."""
    echo: EchoGuard | None = None
    input_allowed: bool = False
    teleop: TeleopChannel | None = None
    manager: Any = None

    @property
    def state(self) -> str:
        return str(self.pc.connectionState)

    @property
    def speaking(self) -> bool:
        return self.speech is not None and self.speech.speaking

    def say(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
        """Queue int16 mono PCM on the outbound track; returns seconds queued (0 if not speaking)."""
        return self.speech.say(pcm, sample_rate) if self.speech is not None else 0.0

    def stop_speaking(self, reason: str = "client") -> int:
        """Drop queued speech now; emits `voice.speech_cancelled` when anything was dropped."""
        if self.speech is None:
            return 0
        dropped = self.speech.clear()
        if dropped and self.manager is not None:
            data = {
                "rtc_id": self.rtc_id,
                "session_id": self.session_id,
                "reason": reason,
                "dropped_ms": dropped * 20,
            }
            emit = self.manager.svc.emit(self.session_id or "server", "voice.speech_cancelled", data)
            self.manager._spawn(emit)
        return dropped


class RtcManager:
    def __init__(self, services: Services) -> None:
        self.svc = services
        self.peers: dict[str, Peer] = {}
        self.on_audio_frames: Callable[..., Any] | None = None
        """STT hook, see the module docstring."""
        self._bg: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ signaling
    async def offer(
        self,
        sdp: str,
        type_: str = "offer",
        session_id: str | None = None,
        *,
        speak: bool | None = None,
        input_: bool = False,
    ) -> tuple[str, str]:
        """Answer a client offer. Returns `(rtc_id, answer_sdp)`."""
        if input_ and not self.svc.settings.teleop_input_enabled:
            raise PermissionError("teleop input is disabled (settings.teleop_input_enabled = false)")
        if input_ and session_id is None:
            raise ValueError("input: true needs a session_id")
        size = len(sdp.encode())
        if size > MAX_SDP_BYTES:
            raise SdpTooLarge(f"SDP is {size} bytes (limit {MAX_SDP_BYTES})")
        if type_ != "offer":
            raise ValueError("type must be 'offer'")
        if not aiortc_available():
            raise RtcUnavailable(INSTALL_HINT)
        if not sdp.startswith("v=0") or "\nm=application " not in sdp:
            raise ValueError(f"offer needs an SDP with a data channel (create '{CHANNEL_LABEL}' first)")
        if len(self.peers) >= self.svc.settings.rtc_max_peers:
            raise RtcLimitError(f"at most {self.svc.settings.rtc_max_peers} WebRTC peers")
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

        # F5: static rtc_ice_servers + ephemeral TURN REST credentials (settings.turn_secret_ref)
        servers, _ = ice_servers(self.svc.settings, getattr(self.svc, "secrets", None))
        ice = [
            RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential"))
            for s in servers
        ]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice))
        rtc_id = f"rtc_{uuid.uuid4().hex[:12]}"
        peer = Peer(rtc_id=rtc_id, session_id=session_id, pc=pc, input_allowed=input_, manager=self)
        self.peers[peer.rtc_id] = peer  # reserve the slot before the first await
        pc.on("datachannel", partial(self._on_datachannel, peer))
        pc.on("track", partial(self._on_track, peer))
        pc.on("connectionstatechange", partial(self._on_state, peer))
        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
            if self._speaks(speak) and any(t.kind == "audio" for t in pc.getTransceivers()):
                peer.echo = EchoGuard()
                peer.speech = SpeechQueue(peer.echo)
                pc.addTrack(outbound_track(peer.speech))  # reuses the offered audio transceiver
            await pc.setLocalDescription(await pc.createAnswer())
        except Exception as exc:
            self.peers.pop(peer.rtc_id, None)
            with contextlib.suppress(Exception):
                await pc.close()
            log.info("rtc offer rejected (%s): %s", redact_sdp(sdp), type(exc).__name__)
            raise ValueError(f"invalid offer ({type(exc).__name__})") from None
        log.info("rtc %s: offer answered (%s)", peer.rtc_id, redact_sdp(sdp))
        await self.svc.audit.record(
            actor="client",
            action="rtc.offer",
            session_id=session_id,
            detail={
                "rtc_id": peer.rtc_id,
                "sdp_bytes": size,
                "audio": peer.has_audio,
                "speak": peer.speech is not None,
                "input": input_,
            },
        )
        return peer.rtc_id, str(pc.localDescription.sdp)

    async def close(self, rtc_id: str, reason: str = "client") -> bool:
        """Idempotent: False when the peer is unknown or already closed."""
        peer = self.peers.pop(rtc_id, None)
        if peer is None:
            return False
        if peer.events is not None:
            await peer.events.stop()
        if peer.teleop is not None:
            await peer.teleop.stop()
        if peer.speech is not None:
            peer.speech.clear()
        current = asyncio.current_task()
        for t in peer.media_tasks:
            if t is not current:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        with contextlib.suppress(Exception):
            await peer.pc.close()
        log.info("rtc %s: closed (%s)", rtc_id, reason)
        try:
            await self.svc.audit.record(
                actor="client" if reason == "client" else "server",
                action="rtc.close",
                session_id=peer.session_id,
                detail={"rtc_id": rtc_id, "reason": reason},
            )
            await self.svc.emit(
                "server", "rtc.closed", {"rtc_id": rtc_id, "session_id": peer.session_id, "reason": reason}
            )
        except Exception:  # closing must not fail on bookkeeping (e.g. during shutdown)
            log.exception("rtc %s: close bookkeeping failed", rtc_id)
        return True

    async def close_all(self) -> None:
        for rtc_id in list(self.peers):
            await self.close(rtc_id, "shutdown")
        for t in list(self._bg):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"rtc_id": p.rtc_id, "session_id": p.session_id, "state": p.state, "created_at": p.created_at}
            for p in self.peers.values()
        ]

    # ------------------------------------------------------------ callbacks
    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    def _speaks(self, speak: bool | None) -> bool:
        st = self.svc.settings
        return bool(st.voice_enabled and st.voice_speak_over_rtc and speak is not False)

    def _on_datachannel(self, peer: Peer, channel: Any) -> None:
        if channel.label == INPUT_LABEL:
            self._on_input_channel(peer, channel)
            return
        if channel.label != CHANNEL_LABEL or peer.events is not None or peer.rtc_id not in self.peers:
            log.info("rtc %s: ignoring data channel %r", peer.rtc_id, channel.label)
            return
        ec = EventChannel(self.svc, peer.rtc_id, channel, partial(self.close, peer.rtc_id))
        peer.events = ec
        channel.on("message", ec.feed)
        channel.on("close", lambda: self._spawn(self.close(peer.rtc_id, "channel_closed")))
        if channel.readyState == "open":  # aiortc opens it before emitting `datachannel`
            self._spawn(ec.start(peer.session_id))
        else:
            channel.on("open", lambda: self._spawn(ec.start(peer.session_id)))

    def _on_input_channel(self, peer: Peer, channel: Any) -> None:
        allowed = peer.input_allowed and self.svc.settings.teleop_input_enabled
        if not allowed or peer.teleop is not None or peer.session_id is None or peer.rtc_id not in self.peers:
            log.info("rtc %s: ignoring %r (teleop input not enabled)", peer.rtc_id, channel.label)
            return
        peer.teleop = TeleopChannel(self.svc, peer.rtc_id, peer.session_id, channel)
        channel.on("message", peer.teleop.feed)

    def _on_track(self, peer: Peer, track: Any) -> None:
        if track.kind == "audio" and not peer.has_audio:
            peer.has_audio = True
            meter = LevelMeter(self.svc.settings.rtc_audio_level_interval_s)
            job = consume_audio(
                track, meter, partial(self._emit_level, peer), partial(self._frames_hook, peer)
            )
        elif track.kind == "video" and peer.session_id and self.svc.settings.perception_sample_rtc_video:
            from auoreo.perception.rtc import sample_video  # W2-C: frames → `rtc` sensor source

            job = sample_video(track, peer.session_id, self.svc.settings.perception_rtc_interval_s)
        else:
            job = drain_track(track)
        peer.media_tasks.append(asyncio.get_running_loop().create_task(job))

    def _on_state(self, peer: Peer) -> None:
        state = peer.state
        if state in {"failed", "closed"} and peer.rtc_id in self.peers:
            self._spawn(self.close(peer.rtc_id, state))

    def _frames_hook(self, peer: Peer) -> FramesHook | None:
        hook = self.on_audio_frames
        if hook is None:
            return None
        return lambda pcm: hook(peer.rtc_id, peer.session_id, pcm, SAMPLE_RATE)

    async def _emit_level(self, peer: Peer, rms: float) -> None:
        await self.svc.emit(
            "server",
            "rtc.audio_level",
            {
                "rtc_id": peer.rtc_id,
                "session_id": peer.session_id,
                "rms": round(rms, 4),
                "speaking": rms > self.svc.settings.rtc_speaking_rms,
            },
        )


def manager_for(svc: Services) -> RtcManager:
    """The process-wide `RtcManager` (`svc.rtc`), created lazily; shared by `/api/rtc/*` and
    the `rtc_transport/aiortc` backend."""
    if svc.rtc is None:
        svc.rtc = RtcManager(svc)
        if (voice := svc.ext.get("voice")) is not None:  # M4c: live transcription hook
            voice.attach(svc.rtc)
    mgr: RtcManager = svc.rtc
    return mgr
