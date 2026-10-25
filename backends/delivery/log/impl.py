"""Log delivery: one redacted JSON line per result in `<home>/deliveries.log` + `delivery.sent`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.delivery import DeliveryProvider, DeliveryReceipt
from auoreo.core.paths import home_dir
from auoreo.core.redaction import redact

MAX_TEXT = 4000


class Backend(DeliveryProvider):
    @property
    def path(self) -> Path:
        home = self.settings.home if self.settings is not None else home_dir()
        return Path(home) / "deliveries.log"

    async def check(self) -> BackendStatus:
        return BackendStatus(True, f"appends to {self.path}")

    async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": meta.get("kind", ""),
            "source": meta.get("source", ""),
            "external_id": meta.get("external_id", ""),
            "session_id": meta.get("session_id"),
            "target": target,
            "text": redact(text)[:MAX_TEXT],
        }
        await asyncio.to_thread(self._append, json.dumps(record, ensure_ascii=False) + "\n")
        if self.services is not None:
            keys = ("kind", "source", "external_id", "session_id")
            await self.services.emit(
                "server", "delivery.sent", {k: record[k] for k in keys} | {"provider": "log"}
            )
        return DeliveryReceipt(True, f"appended to {self.path.name}")

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
