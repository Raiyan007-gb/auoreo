"""I8 extension starter (`core/extensions.STARTERS` → `services.ext["i8_boot"]`).

Attaches the I8 runtime pieces, each only when configured:
- the computer-use turn-end watcher (`tools.cu_safety.TurnEndWatcher`: `unhide()` + lock
  release when a run of a session that used `computer` ends; `i8.computer_use.unhide_on_turn_end`);
- the wake-word daemon (`voice.wake.WakeDaemon`) when `i8.wake.enabled` (off by default);
- the in-process LiveKit worker (`voice.livekit_worker.LiveKitWorker`) when
  `i8.livekit.worker.enabled`.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from auoreo.core.settings_i8 import i8_of

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)


class I8Runtime:
    def __init__(self) -> None:
        self.cu_watch: Any = None
        self.wake: Any = None
        self.livekit: Any = None

    async def close(self) -> None:
        for part in (self.livekit, self.wake, self.cu_watch):
            if part is not None:
                with contextlib.suppress(Exception):
                    await part.close()


async def start(services: Services) -> I8Runtime:
    cfg = i8_of(services.settings)
    rt = I8Runtime()
    if cfg.computer_use.unhide_on_turn_end:
        from auoreo.tools.cu_safety import TurnEndWatcher

        rt.cu_watch = TurnEndWatcher(services)  # subscribes lazily, on the first `computer` call
    if cfg.wake.enabled and services.settings.voice_enabled:
        from auoreo.voice.wake import WakeDaemon

        rt.wake = WakeDaemon(services)
        try:
            await rt.wake.start()
        except Exception as exc:  # a missing microphone must not stop the server
            log.warning("wake daemon failed to start: %s", exc)
            rt.wake.status, rt.wake.detail = "error", str(exc)[:300]
    if cfg.livekit.worker.enabled:
        from auoreo.voice.livekit_worker import LiveKitWorker

        rt.livekit = LiveKitWorker(services)
        await rt.livekit.start()
    return rt
