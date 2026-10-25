"""LiveKit telephony (F5; spec §26.8): SIP DTMF in/out, a digit → action IVR, cold and warm
transfer. Mixed into `impl.Backend`; loaded by file path (backend folders are not packages).

Realtime pieces use the `livekit` realtime SDK (`livekit.rtc`, extra `livekit`): the agent joins
the call room as a hidden-from-subscription `agent`-kind participant, listens for
`sip_dtmf_received` (RFC 4733 telephone-events relayed by livekit-sip) and sends tones with
`LocalParticipant.publish_dtmf(code, digit)`. Without the SDK those calls raise
`LiveKitUnavailable`; transfers still work (server API only) but skip the spoken announcement.

Warm transfer: dial the target with `CreateSIPParticipant(wait_until_answered)` into a consult
room, speak the announcement there, then `MoveParticipant` the target into the caller's room
(LiveKit Cloud / Private Cloud only). On a self-hosted server (no MoveParticipant) the target
is dialled straight into the caller's room and the announcement is heard by both. The agent
then leaves. Every transfer emits `rtc.transfer` and is audited as `rtc.transfer`.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

DTMF_CODES = {**{str(d): d for d in range(10)}, "*": 10, "#": 11, "A": 12, "B": 13, "C": 14, "D": 15}
AGENT_IDENTITY = "auoreo-agent"
TARGET_IDENTITY = "transfer-target"
TONE_GAP_S = 0.12


def is_cloud(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "livekit.cloud" or host.endswith(".livekit.cloud")


def rtc_sdk() -> Any:
    try:
        from livekit import rtc
    except ImportError as exc:
        raise RuntimeError("the livekit realtime SDK is not installed; install auoreo[livekit]") from exc
    return rtc


def tel_uri(target: str) -> str:
    """`+15550100` → `tel:+15550100`; `tel:`/`sip:` URIs pass through; anything else is refused."""
    t = target.strip()
    if t.startswith(("tel:", "sip:")):
        return t
    digits = t.replace(" ", "").replace("-", "")
    if digits.startswith("+") and digits[1:].isdigit() and 6 <= len(digits) <= 16:
        return f"tel:{digits}"
    raise ValueError("target must be an E.164 number (+15550100) or a tel:/sip: URI")


def dial_number(target: str) -> str:
    """`CreateSIPParticipant.sip_call_to` wants the bare number / SIP user."""
    uri = tel_uri(target)
    return uri[4:] if uri.startswith(("tel:", "sip:")) else uri


def check_digits(digits: str) -> str:
    bad = [d for d in digits if d not in DTMF_CODES and d not in "w "]
    if not digits or bad:
        raise ValueError(f"DTMF digits must be 0-9 * # A-D (w = pause); got {digits!r}")
    return digits


class TelephonyMixin:
    """Needs from the host class: `cfg`, `rooms`, `links`, `_tasks`, `rtc_factory`, `_url()`,
    `_call()`, `mint_token()` and `transfer()`."""

    rooms: dict[str, dict[str, Any]]
    links: dict[str, Any]
    """room → connected realtime `Room` of the agent."""
    _tasks: set[asyncio.Task[Any]]
    svc: Any = None
    rtc_factory: Callable[[], Any] | None = None

    # host-class API (implemented in impl.Backend)
    cfg: Any
    _url: Callable[[], str]
    _call: Callable[..., Any]
    mint_token: Callable[..., str]
    transfer: Callable[..., Any]

    tel_uri = staticmethod(tel_uri)

    def bind(self, svc: Any) -> None:
        """Attach services for events, audit and IVR follow-ups (the API router calls this)."""
        self.svc = svc

    # ------------------------------------------------------------ plumbing
    def _session_of(self, room: str) -> str:
        info = self.rooms.get(room) or {}
        return str(info.get("session_id") or "server")

    async def _emit(self, room: str, type_: str, data: dict[str, Any]) -> None:
        if self.svc is not None:
            await self.svc.emit(self._session_of(room), type_, {"room": room, **data})

    async def _audit(self, room: str, action: str, detail: dict[str, Any], decision: str = "allow") -> None:
        if self.svc is not None:
            sid = self._session_of(room)
            await self.svc.audit.record(
                actor="agent",
                action=action,
                session_id=None if sid == "server" else sid,
                decision=decision,
                detail={"room": room, **detail},
            )

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------- agent presence
    async def connect_agent(self, room: str, identity: str = AGENT_IDENTITY) -> Any:
        """Join `room` as the agent (realtime SDK) and start listening for SIP DTMF."""
        if room in self.links:
            return self.links[room]
        url = self._url()
        link = self.rtc_factory() if self.rtc_factory is not None else rtc_sdk().Room()
        link.on("sip_dtmf_received", lambda ev: self._spawn(self._on_dtmf(room, ev)))
        await link.connect(url, self.mint_token(identity, room, kind="agent"))
        self.links[room] = link
        return link

    async def disconnect_agent(self, room: str) -> bool:
        link = self.links.pop(room, None)
        if link is None:
            return False
        await link.disconnect()
        return True

    async def _on_dtmf(self, room: str, ev: Any) -> None:
        participant = getattr(getattr(ev, "participant", None), "identity", None) or ""
        await self.handle_dtmf(room, participant, str(getattr(ev, "digit", "")))

    async def handle_dtmf(self, room: str, participant: str, digit: str) -> str | None:
        """Emit `rtc.dtmf` and run the IVR action bound to `digit` (if any). Returns the action."""
        action = dict(self.cfg.sip.ivr).get(digit)
        await self._emit(room, "rtc.dtmf", {"participant": participant, "digit": digit, "action": action})
        if not action:
            return None
        kind, _, arg = action.partition(":")
        handlers: dict[str, Callable[[], Any]] = {
            "transfer": lambda: self.transfer(room, participant, tel_uri(arg), play_dialtone=True),
            "say": lambda: self.say(room, arg),
            "hangup": lambda: self.hangup(room, participant),
            "followup": lambda: self._followup(room, participant, digit, arg),
        }
        try:
            await handlers[kind]()
        except (ValueError, ConnectionError, RuntimeError, PermissionError) as exc:
            await self._emit(
                room, "rtc.ivr_failed", {"digit": digit, "action": kind, "error": str(exc)[:200]}
            )
        return action

    async def _followup(self, room: str, participant: str, digit: str, text: str) -> None:
        sid = self._session_of(room)
        if self.svc is None or sid == "server":
            raise RuntimeError(f"room {room} is not bound to a session; followup dropped")
        await self.svc.coordinator.submit(sid, f"[caller {participant} pressed {digit}] {text}")

    async def hangup(self, room: str, participant: str) -> None:
        api = self._api_sdk()
        req = api.RoomParticipantIdentity(room=room, identity=participant)
        await self._call(lambda lk: lk.room.remove_participant(req))
        await self._audit(room, "rtc.hangup", {"participant": participant})

    async def send_dtmf(self, room: str, digits: str) -> int:
        """Play `digits` to the phone participants of `room` (RFC 4733 via livekit-sip).
        `w` waits 0.5 s. Returns the number of tones sent."""
        check_digits(digits)
        link = await self.connect_agent(room)
        sent = 0
        for d in digits:
            if d in "w ":
                await asyncio.sleep(0.5)
                continue
            await link.local_participant.publish_dtmf(code=DTMF_CODES[d], digit=d)
            sent += 1
            await asyncio.sleep(TONE_GAP_S)
        await self._emit(room, "rtc.dtmf_sent", {"count": sent})
        await self._audit(room, "rtc.dtmf_sent", {"count": sent})  # never the digits (PINs)
        return sent

    async def say(self, room: str, text: str) -> bool:
        """Speak `text` into `room` through the voice service's TTS. False (skipped) when the
        realtime SDK or the voice service is unavailable."""
        voice = getattr(self.svc, "ext", {}).get("voice") if self.svc is not None else None
        if voice is None or not text.strip():
            return False
        try:
            rtc: Any = rtc_sdk()
        except RuntimeError:
            if self.rtc_factory is None:
                return False
            rtc = None
        from auoreo.voice.audio import read_wav

        audio = await voice.speak(text, session_id=self._session_of(room))
        if getattr(audio, "media_type", "") not in {"audio/wav", "audio/x-wav"}:
            return False
        wav = read_wav(audio.data)
        link = await self.connect_agent(room)
        player = getattr(link, "play_pcm", None)  # fakes / future helpers
        if player is not None:
            await player(wav.pcm, wav.sample_rate, wav.channels)
        elif rtc is not None:
            await _publish_pcm(rtc, link, wav.pcm, wav.sample_rate, wav.channels)
        await self._emit(room, "rtc.said", {"chars": len(text)})
        return True

    def _api_sdk(self) -> Any:
        from livekit import api

        return api

    # ------------------------------------------------------------ transfer
    def sip_participant_request(self, room: str, target: str, identity: str = TARGET_IDENTITY) -> Any:
        """`CreateSIPParticipantRequest` for dialling `target` (Krisp / region from settings)."""
        trunk = self.cfg.sip.outbound_trunk_id
        if not trunk:
            raise ValueError("settings.livekit.sip.outbound_trunk_id is not set (needed to dial the target)")
        from livekit.protocol import sip

        req = sip.CreateSIPParticipantRequest(
            sip_trunk_id=trunk,
            sip_call_to=dial_number(target),
            room_name=room,
            participant_identity=identity,
            participant_name="transfer target",
            wait_until_answered=True,
            krisp_enabled=bool(self.cfg.sip.krisp_enabled),
            media_encryption=sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
        )
        req.ringing_timeout.FromTimedelta(timedelta(seconds=30))
        if self.cfg.region:
            req.destination.region = str(self.cfg.region)
        return req

    async def warm_transfer(
        self,
        room: str,
        caller_identity: str,
        target_number: str,
        *,
        announce_text: str | None = None,
        agent_identity: str = AGENT_IDENTITY,
    ) -> dict[str, Any]:
        """Consult the target, announce the caller, bridge both, drop the agent."""
        api = self._api_sdk()
        moved = is_cloud(self.cfg.url)  # MoveParticipant: Cloud / Private Cloud only
        consult = f"{room}-consult-{uuid.uuid4().hex[:6]}"[:128] if moved else room
        if moved:
            create = api.CreateRoomRequest(name=consult, empty_timeout=60, max_participants=4)
            await self._call(lambda lk: lk.room.create_room(create))
        dial = self.sip_participant_request(consult, target_number)
        await self._call(lambda lk: lk.sip.create_sip_participant(dial))
        announced = bool(announce_text) and await self.say(consult, str(announce_text))
        if moved:
            move = api.MoveParticipantRequest(room=consult, identity=TARGET_IDENTITY, destination_room=room)
            await self._call(lambda lk: lk.room.move_participant(move))
            await self.disconnect_agent(consult)
            delete = api.DeleteRoomRequest(room=consult)
            await self._call(lambda lk: lk.room.delete_room(delete))
        await self.disconnect_agent(room)
        leave = api.RoomParticipantIdentity(room=room, identity=agent_identity)
        with contextlib.suppress(ConnectionError):  # the agent may be a separate worker: best-effort
            await self._call(lambda lk: lk.room.remove_participant(leave))
        out = {
            "kind": "warm",
            "caller": caller_identity,
            "target": dial_number(target_number)[-4:].rjust(8, "*"),
            "bridge": "move" if moved else "in_room",
            "announced": announced,
            "consult_room": consult if moved else None,
        }
        await self._emit(room, "rtc.transfer", out)
        await self._audit(room, "rtc.transfer", out)
        return out


async def _publish_pcm(rtc: Any, link: Any, pcm: bytes, rate: int, channels: int) -> None:
    """Publish int16 PCM as a one-off microphone track in 10 ms frames, then unpublish."""
    source = rtc.AudioSource(rate, channels)
    track = rtc.LocalAudioTrack.create_audio_track("auoreo-say", source)
    pub = await link.local_participant.publish_track(track)
    step = rate // 100 * channels * 2
    for i in range(0, len(pcm) - len(pcm) % 2, step):
        chunk = pcm[i : i + step]
        n = len(chunk) // (2 * channels)
        await source.capture_frame(rtc.AudioFrame(chunk, rate, channels, n))
    await source.wait_for_playout()
    await link.local_participant.unpublish_track(pub.sid)
