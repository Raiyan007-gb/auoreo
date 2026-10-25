"""Team blackboard (spec L1024): keyed artifacts per team, read by tools, never mailbox dumps.

The mailbox stays control-plane only: big results go on the board and the mail carries a
pointer (`board:<key>`) plus a ≤10-word summary. Values are ≤ 64 KiB UTF-8; `put` upserts
and bumps `version`; every write emits `board.put` (or `board.delete`) on the `server`
aggregate. Values that look like secrets are refused (same screen as the event redactor).
"""

from __future__ import annotations

import builtins
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import ArtifactRow, utcnow
from auoreo.core.redaction import redact
from auoreo.teams.mailbox import TeamsError

if TYPE_CHECKING:
    from auoreo.core.services import Services

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}$")
MAX_VALUE_BYTES = 64 * 1024
MAX_LIST = 500


def check_key(key: str) -> str:
    if not KEY_RE.fullmatch(key) or "//" in key or ".." in key:
        raise TeamsError(f"invalid board key '{key}': use [a-z0-9._/-] (≤200 chars, starts with [a-z0-9])")
    return key


def looks_secret(value: str) -> bool:
    return "PRIVATE KEY" in value or redact(value) != value


def artifact_dict(row: ArtifactRow, *, with_value: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "team_id": row.team_id,
        "key": row.key,
        "producer": row.producer,
        "version": row.version,
        "bytes": len(row.value.encode("utf-8")),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if with_value:
        out["value"] = row.value
    return out


class Board:
    def __init__(self, services: Services) -> None:
        self.s = services

    async def put(self, team_id: str, key: str, value: str, producer: str) -> ArtifactRow:
        check_key(key)
        size = len(value.encode("utf-8"))
        if size > MAX_VALUE_BYTES:
            raise TeamsError(
                f"board value is {size} bytes; the limit is {MAX_VALUE_BYTES} (64 KiB): "
                "split it over several keys"
            )
        if looks_secret(value):
            raise TeamsError("refusing to put something that looks like a secret on the board")
        producer = producer[:64]
        async with self.s.db.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.team_id == team_id, ArtifactRow.key == key)
            row = (await s.execute(q)).scalar()
            if row is None:
                row = ArtifactRow(team_id=team_id, key=key, value=value, producer=producer, version=1)
                s.add(row)
            else:
                row.value, row.producer, row.version, row.updated_at = (
                    value,
                    producer,
                    row.version + 1,
                    utcnow(),
                )
            await s.commit()
            await s.refresh(row)
        await self.s.emit(
            "server",
            "board.put",
            {"team_id": team_id, "key": key, "version": row.version, "producer": producer, "bytes": size},
        )
        return row

    async def get(self, team_id: str, key: str) -> ArtifactRow | None:
        check_key(key)
        async with self.s.db.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.team_id == team_id, ArtifactRow.key == key)
            return (await s.execute(q)).scalar()

    async def list(self, team_id: str, prefix: str = "") -> builtins.list[ArtifactRow]:
        async with self.s.db.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.team_id == team_id).order_by(ArtifactRow.key)
            if prefix:
                q = q.where(ArtifactRow.key.startswith(prefix, autoescape=True))
            return list((await s.execute(q.limit(MAX_LIST))).scalars())

    async def delete(self, team_id: str, key: str, producer: str = "") -> bool:
        row = await self.get(team_id, key)
        if row is None:
            return False
        async with self.s.db.session() as s:
            await s.delete(await s.merge(row))
            await s.commit()
        await self.s.emit("server", "board.delete", {"team_id": team_id, "key": key, "producer": producer})
        return True
