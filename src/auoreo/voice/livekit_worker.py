"""In-process `livekit-agents` worker (spec §26.6/§26.7; P1 opt-in, off by default).

Started by the I8 starter when `i8.livekit.worker.enabled` and `backends.rtc_transport =
"livekit"`. `livekit-agents` is imported lazily and is NOT a declared extra: every 1.x release
pins OpenTelemetry/OpenAI versions that conflict with the `otel` extra and LiteLLM, so it is
installed by hand into the server venv (or a separate one) — `status` says so honestly.

Shape: `AgentServer(ws_url, api_key, api_secret, job_executor_type=THREAD)` (thread jobs, so
the entrypoint can reach `Services`) with `@server.rtc_session(agent_name=…)` explicit
dispatch. Per job:

1. `ctx.connect()`; the room's AUOREO session is the one `POST /api/rtc/join` or
   `POST /api/livekit/token` recorded for it, else a new session (`worker.default_agent`).
2. A `LiveKitLink` peer joins the `RtcManager`, so transcription, barge-in, the echo guard,
   the filter and spoken replies are the SAME code (and events) as the aiortc transport.
3. `AgentSession(stt=<tap>, turn_handling={turn_detection: "manual"})` owns room I/O: its STT node is a tap
   whose `RecognizeStream` resamples the participant's audio to 16 kHz and feeds the link (it
   never produces transcripts itself — AUOREO endpointing does), and its own audio output is
   off (`RoomOptions(audio_output=False)`): the link publishes `auoreo-voice`. Barge-in maps to
   `session.interrupt(force=True)`. Approvals never travel over LiveKit data/RPC (§26.9).

`agents` / `rtc` namespaces are injectable (tests use fakes; no LiveKit server here).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from auoreo.core.settings_i8 import i8_of

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

INSTALL = (
    "uv pip install 'livekit-agents>=1.8,<2' into the server venv (not an extra: it conflicts "
    "with the otel extra and LiteLLM's openai pin)"
)


class WorkerUnavailable(RuntimeError):
    pass


def load_agents() -> tuple[Any, Any]:
    try:
        from livekit import agents, rtc  # type: ignore[attr-defined,unused-ignore]
    except ImportError as exc:
        raise WorkerUnavailable(f"livekit-agents is not installed; {INSTALL}") from exc
    return agents, rtc


def tap_stt(agents: Any, feed: Any) -> Any:
    """An `agents.stt.STT` whose stream forwards 16 kHz int16 audio to `feed(bytes)`."""
    stt_mod = agents.stt

    class TapStream(stt_mod.RecognizeStream):  # type: ignore[misc,name-defined]
        async def _run(self) -> None:
            async for item in self._input_ch:
                data = getattr(item, "data", None)
                if data is not None:
                    feed(bytes(data))

    class TapSTT(stt_mod.STT):  # type: ignore[misc,name-defined]
        def __init__(self) -> None:
            super().__init__(capabilities=stt_mod.STTCapabilities(streaming=True, interim_results=False))

        async def _recognize_impl(
            self, buffer: Any, *, language: Any = None, conn_options: Any = None
        ) -> Any:
            return stt_mod.SpeechEvent(type=stt_mod.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])

        def stream(self, *, language: Any = None, conn_options: Any = None) -> Any:
            opts = conn_options if conn_options is not None else agents.DEFAULT_API_CONNECT_OPTIONS
            return TapStream(stt=self, conn_options=opts, sample_rate=16_000)

    return TapSTT()


class LiveKitWorker:
    def __init__(self, services: Services, *, agents: Any = None, rtc: Any = None) -> None:
        self.svc = services
        self.agents, self.rtc = agents, rtc
        self.status = "stopped"
        self.detail = ""
        self.server: Any = None
        self.links: dict[str, Any] = {}
        self.sessions: dict[str, Any] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def cfg(self) -> Any:
        return i8_of(self.svc.settings).livekit.worker

    def transport(self) -> Any:
        inst = self.svc.backend("rtc_transport")
        if getattr(inst, "name", "") != "livekit":
            raise WorkerUnavailable("backends.rtc_transport is not 'livekit'")
        return inst

    async def start(self) -> bool:
        try:
            lk = self.transport()
            url, (key, secret) = lk._url(), lk._keys()
            if self.agents is None:
                self.agents, self.rtc = load_agents()
        except (WorkerUnavailable, RuntimeError, PermissionError, KeyError) as exc:
            self.status, self.detail = "unavailable", str(exc)
            log.warning("livekit worker not started: %s", exc)
            return False
        a = self.agents
        self.server = a.AgentServer(
            ws_url=url, api_key=key, api_secret=secret, job_executor_type=a.JobExecutorType.THREAD
        )
        self.server.rtc_session(agent_name=self.cfg.agent_name)(self.entrypoint)
        self._task = asyncio.get_running_loop().create_task(self._run(), name="livekit:worker")
        self.status, self.detail = "running", f"agent {self.cfg.agent_name!r} on {url}"
        return True

    async def _run(self) -> None:
        try:
            await self.server.run(devmode=False, unregistered=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status, self.detail = "error", f"{type(exc).__name__}: {exc}"[:300]
            log.warning("livekit worker stopped: %s", exc)

    async def session_for(self, room: str) -> str | None:
        with contextlib.suppress(WorkerUnavailable, KeyError, LookupError):
            known = self.transport().rooms.get(room)
            if known and known.get("session_id"):
                return str(known["session_id"])
        sess = await self.svc.create_session(title=f"livekit {room[:8]}", agent=self.cfg.default_agent)
        return str(sess.id)

    async def entrypoint(self, ctx: Any) -> None:
        """One dispatched job: bridge the room into the AUOREO voice plane."""
        from auoreo.rtc.peers import manager_for
        from auoreo.voice.livekit_link import LiveKitLink

        a, rtc = self.agents, self.rtc
        await ctx.connect()
        room = ctx.room
        session_id = await self.session_for(str(room.name))
        holder: dict[str, Any] = {}

        async def publish(track: Any) -> Any:
            opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            return await room.local_participant.publish_track(track, opts)

        def interrupt() -> Any:
            session = holder.get("session")
            return session.interrupt(force=True) if session is not None else None

        link = LiveKitLink(
            self.svc, manager_for(self.svc), room=str(room.name), session_id=session_id,
            rtc=rtc, publish=publish, interrupt=interrupt,
        )  # fmt: skip
        turns = a.TurnHandlingOptions(turn_detection="manual")
        session = a.AgentSession(stt=tap_stt(a, link.feed), turn_handling=turns)
        holder["session"] = session
        await link.start()
        self.links[link.rtc_id], self.sessions[link.rtc_id] = link, session
        agent = a.Agent(instructions="AUOREO voice bridge: turns are handled by the AUOREO runtime.")
        options = a.voice.room_io.RoomOptions(audio_output=False, text_output=False)
        await session.start(agent=agent, room=room, room_options=options)

        async def shutdown(*_: Any) -> None:
            self.links.pop(link.rtc_id, None)
            self.sessions.pop(link.rtc_id, None)
            await link.close("livekit_job_ended")

        ctx.add_shutdown_callback(shutdown)

    async def close(self) -> None:
        for link in list(self.links.values()):
            with contextlib.suppress(Exception):
                await link.close("shutdown")
        self.links.clear()
        if self.server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.aclose(), 10)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        self.status = "stopped"
