"""Composition root: builds every service once and hands them to API/CLI/runtime."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.approvals import ApprovalBroker
from auoreo.core.audit import AuditLog
from auoreo.core.bus import EventBus
from auoreo.core.connections import ConnectionManager
from auoreo.core.db import Database, SessionRow, utcnow
from auoreo.core.event_store import EventStore
from auoreo.core.idempotency import Idempotency
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.questions import QuestionBroker
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings
from auoreo.gateway.resolve import make_gateway
from auoreo.gateway.types import Gateway
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness

if TYPE_CHECKING:
    from auoreo.mcp.bridge import McpBridge
    from auoreo.memory.service import MemoryService
    from auoreo.runtime.coordinator import SessionRunCoordinator


log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    cwd: Path
    harness: Harness
    db: Database
    bus: EventBus
    events: EventStore
    audit: AuditLog
    idempotency: Idempotency
    secrets: SecretStore
    gateway: Gateway
    approvals: ApprovalBroker = field(init=False)
    questions: QuestionBroker = field(init=False)
    coordinator: SessionRunCoordinator = field(init=False)
    connections: ConnectionManager = field(init=False)
    memory: MemoryService | None = None
    mcp: McpBridge | None = None
    rtc: Any = None  # auoreo.rtc.peers.RtcManager, created on first /api/rtc use

    def __post_init__(self) -> None:
        self._backends: dict[str, Any] = {}
        self.approvals = ApprovalBroker(self.emit, self.settings.approval_timeout_s)
        self.questions = QuestionBroker(self.emit, self.settings.approval_timeout_s)
        self.connections = ConnectionManager()

    async def emit(self, aggregate_id: str, type_: str, data: dict[str, Any]) -> None:
        await self.events.append(aggregate_id, type_, data)

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        cwd: Path | None = None,
        gateway: Gateway | None = None,
        db_path: Path | str | None = None,
        with_memory: bool = True,
        with_mcp: bool = True,
    ) -> Services:
        from auoreo.runtime.coordinator import SessionRunCoordinator

        cwd = (cwd or Path.cwd()).resolve()
        home = ensure_home(settings.home)
        harness = Harness(
            default_roots(home, cwd, bundled_root(), settings.extra_roots),
            strict_overlays=settings.strict_overlays,
        )
        db = Database(db_path or settings.db_path)
        await db.init()
        bus = EventBus()
        secrets = SecretStore(home, settings.keyring)
        svc = cls(
            settings=settings,
            cwd=cwd,
            harness=harness,
            db=db,
            bus=bus,
            events=EventStore(db, bus, settings.redact_secrets),
            audit=AuditLog(db),
            idempotency=Idempotency(db),
            secrets=secrets,
            gateway=gateway or make_gateway(settings, secrets.get),
        )
        svc.coordinator = SessionRunCoordinator(svc)
        if with_memory:
            from auoreo.memory.service import MemoryService

            svc.memory = await MemoryService.open(svc)
        if with_mcp:
            from auoreo.mcp.bridge import McpBridge

            svc.mcp = McpBridge(svc)
            await svc.mcp.start()
        return svc

    # ------------------------------------------------------------ backends
    def backend(self, kind: str) -> Any:
        """Selected backend instance for `kind`, constructed once per process."""
        if kind in self._backends:
            return self._backends[kind]
        chosen = self.settings.backends.get(kind)
        bdef = self.harness.select_backend(kind, chosen)
        if bdef is None:
            raise KeyError(
                f"no '{kind}' backend selected; add backends/{kind}/<name>/ or set settings.backends.{kind}"
            )
        inst = bdef.load()(self.settings)
        self._backends[kind] = inst
        return inst

    async def close_backends(self) -> None:
        for kind, inst in list(self._backends.items()):
            close = getattr(inst, "close", None)
            if close is None:
                continue
            try:
                res = close()
                if hasattr(res, "__await__"):
                    await res
            except Exception:  # shutdown must not fail on a backend
                log.exception("backend %s failed to close", kind)
        self._backends.clear()

    async def close(self) -> None:
        if self.rtc is not None:  # peers emit rtc.closed, so before the DB goes away
            await self.rtc.close_all()
        await self.connections.close_all(1001)  # before the DB goes away
        await self.close_backends()
        await self.coordinator.shutdown()
        if self.mcp is not None:
            await self.mcp.stop()
        if self.memory is not None:
            self.memory.close()
        await self.db.close()

    # ------------------------------------------------------------ sessions
    async def create_session(
        self,
        *,
        title: str | None = None,
        agent: str | None = None,
        workflow: str | None = None,
        model: str | None = None,
        parent_id: str | None = None,
    ) -> SessionRow:
        agent = agent or self.settings.agent
        workflow = workflow or self.settings.workflow
        self.harness.agent(agent)  # validate early
        if workflow not in self.harness.workflows:
            raise KeyError(f"unknown workflow '{workflow}' (known: {sorted(self.harness.workflows)})")
        row = SessionRow(
            id=f"ses_{uuid.uuid4().hex[:16]}",
            title=title or "",
            agent=agent,
            workflow=workflow,
            model=model,
            parent_id=parent_id,
        )
        async with self.db.session() as s:
            s.add(row)
            await s.commit()
        await self.emit(
            row.id,
            "session.created",
            {"agent": agent, "workflow": workflow, "parent_id": parent_id},
        )
        return row

    async def get_session(self, session_id: str) -> SessionRow | None:
        async with self.db.session() as s:
            return await s.get(SessionRow, session_id)

    async def list_sessions(self, limit: int = 100, *, include_archived: bool = False) -> list[SessionRow]:
        """Newest first. Archived sessions are hidden unless `include_archived`."""
        async with self.db.session() as s:
            q = select(SessionRow).order_by(SessionRow.updated_at.desc()).limit(limit)
            if not include_archived:
                q = q.where(SessionRow.archived.is_(False))
            return list((await s.execute(q)).scalars())

    async def update_session(self, session_id: str, **fields: Any) -> SessionRow | None:
        async with self.db.session() as s:
            row = await s.get(SessionRow, session_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            await s.commit()
            return row

    async def set_session_status(self, session_id: str, status: str, title: str | None = None) -> None:
        async with self.db.session() as s:
            row = await s.get(SessionRow, session_id)
            if row is None:
                return
            row.status = status
            row.updated_at = utcnow()
            if title and not row.title:
                row.title = title[:120]
            await s.commit()
