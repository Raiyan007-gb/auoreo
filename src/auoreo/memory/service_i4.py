"""I4 parts of `MemoryService` (spec §11.2, §11.3, §11.7, §24.3, §25.8).

- Injection: one combined MEMORY + USER (+ approved PREFS) excerpt capped at
  `i4.memory.injection_chars` (2000) + recalled items in `<memory-context>`.
- Scope ACL: `session|project|user|team`; a `session` fact is visible only to its
  `owner_session`. Tenants: `local` + one per team (`team:<id>`, provisioned on
  `team.created`); an explicit cross-tenant read is refused and audited
  (`decisionReason: tenant-deny`).
- Decay: a fact with `half_life_days` ranks × 0.5^(age/half_life); `expires_at` ends it.
- Contradictions: top-500 FTS candidates, flagged when entity overlap ≥ 0.3 and the
  polarity/number score ≥ 0.3 (never overwritten).
- M7: `agent:<slug>` tenants for agents that declare `memory_tenant` (`for_agent`).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from auoreo.memory import ledger
from auoreo.memory.contradiction import is_contradiction, top_terms
from auoreo.memory.files import read_capped, truncate
from auoreo.memory.store import MemoryStore, Row
from auoreo.memory.store_search import AGENT_NS

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

MEMORY_TAG = "memory-context"
TYPES = ("user", "feedback", "project", "reference")
"""Closed save taxonomy (§11.3); stored as the fact's `category`."""
SCOPES = ("session", "project", "user", "team")
DEFAULT_VISIBLE = frozenset(SCOPES)
TENANT_DENY = "tenant-deny"


class TenantDenied(PermissionError):
    pass


def visible(row: Row, session_id: str | None, scopes: frozenset[str] = DEFAULT_VISIBLE) -> bool:
    scope = str(row.extra.get("scope") or "user")
    if scope not in scopes:
        return False
    return scope != "session" or (session_id is not None and row.extra.get("owner_session") == session_id)


def decay(row: Row, now: float | None = None) -> float:
    half = row.extra.get("half_life_days")
    created = row.extra.get("created_at")
    if not isinstance(half, int | float) or half <= 0 or not isinstance(created, int | float):
        return 1.0
    age_days = max(0.0, ((now or time.time()) - created) / 86_400)
    return float(0.5 ** (age_days / half))


class I4ServiceMixin:
    s: Services
    store: MemoryStore
    cfg: Any

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # provided by MemoryService
        raise NotImplementedError

    async def search(self, query: str, limit: int = 8, *args: Any, **kwargs: Any) -> list[Any]:
        raise NotImplementedError

    # ------------------------------------------------------------ injection
    def file_excerpt(self) -> str:
        """MEMORY.md + USER.md (+ PREFS.md with approved entries), combined ≤ injection_chars."""
        home = self.s.settings.home
        parts = [read_capped(home, "MEMORY.md", self.cfg.memory_char_limit)]
        parts.append(read_capped(home, "USER.md", self.cfg.user_char_limit))
        prefs = read_capped(home, "PREFS.md", self.cfg.user_char_limit)  # G6: approved prefs only
        parts.append(prefs if "\n- " in prefs else "")
        text = "\n\n".join(p for p in parts if p)
        cap = self.s.settings.i4.memory.injection_chars
        return truncate(text, max_chars=cap, label="MEMORY.md + USER.md + PREFS.md") if text else ""

    async def context_block(
        self, query: str, *, allow_aux: bool = True, limit: int = 8, session_id: str | None = None
    ) -> str:
        """Memory text for the per-turn context snapshot (files + recalled items)."""
        parts = [p for p in [self.file_excerpt()] if p]
        hits = await self.search(query, limit=limit, allow_aux=allow_aux, session_id=session_id)
        if hits:
            budget = self.cfg.auto_inject_tokens * 4
            lines, used = [], 0
            for h in hits:
                line = f"- [{h.citation}] {h.text.strip()}"
                if used + len(line) > budget:
                    break
                lines.append(line)
                used += len(line)
            shown = hits[: len(lines)]
            n_f = sum(1 for h in shown if h.kind == "fact")
            n_g = sum(1 for h in shown if h.kind == "community")
            themes = f" + {n_g} themes" if n_g else ""
            parts.append(
                f"🧠 memory — recalled {n_f} facts + {len(lines) - n_f - n_g} chunks{themes}\n"
                f"<{MEMORY_TAG}>\n" + "\n".join(lines) + f"\n</{MEMORY_TAG}>"
            )
        return "\n\n".join(parts)

    # ------------------------------------------------------- contradictions
    async def flag_contradictions(self, fact_id: int, content: str) -> list[int]:
        """Flag (never overwrite) active facts the new one may contradict (§24.3)."""
        terms = top_terms(content)
        if not terms:
            return []
        mcfg = self.s.settings.i4.memory
        ids = [
            i
            for i in await self._run(self.store.fts, "fact", " ".join(terms), mcfg.contradiction_scan)
            if i != fact_id
        ]
        rows = await self._run(self.store.fetch, "fact", ids)
        hits = [i for i in ids if i in rows and is_contradiction(content, rows[i].text, mcfg.entity_overlap)]
        for existing in hits:
            await self.s.emit("server", "memory.contradiction", {"new_id": fact_id, "existing_id": existing})
        ledger.append(
            self.s.settings.home,
            [
                {
                    **ledger.entry("curator", "flag_contradiction", fact_id, "possible contradiction"),
                    "existing_id": e,
                }
                for e in hits
            ],
        )
        return hits

    # -------------------------------------------------------------- tenants
    async def provision_tenant(self, tenant_id: str, kind: str = "team") -> bool:
        created = bool(await self._run(self.store.provision_tenant, tenant_id, kind))
        if created:
            await self.s.audit.record(
                actor="system", action="memory.tenant_provisioned", detail={"tenant": tenant_id}
            )
        return created

    async def tenant_for_session(self, session_id: str | None) -> str:
        """`team:<id>` when the session is in a team whose tenant exists, else `local`."""
        teams = self.s.ext.get("teams") if session_id else None
        if teams is None:
            return self.store.tenant
        try:
            team = await teams.team_for(session_id)
        except Exception:
            return self.store.tenant
        if team is None:
            return self.store.tenant
        tid = f"team:{team.id}"
        return tid if await self._run(self.store.tenant_exists, tid) else self.store.tenant

    async def authorize_tenant(self, requested: str, *, session_id: str | None, tool: str) -> None:
        """Explicit tenant reads: the local tenant or the session's own team tenant only."""
        allowed = {self.store.tenant, await self.tenant_for_session(session_id)}
        own = requested == self.store.tenant or not requested.startswith(AGENT_NS)  # M7: never cross-agent
        if requested in allowed and own:
            return
        await self.s.audit.record(
            actor="agent",
            action="memory.read",
            session_id=session_id,
            decision="deny",
            reason=TENANT_DENY,
            detail={"tool": tool, "decisionReason": TENANT_DENY, "tenant": requested},
        )
        raise TenantDenied(f"tenant '{requested}' is not readable from this session ({TENANT_DENY})")

    def for_tenant(self, tenant_id: str) -> Any:
        """A `MemoryService` bound to another tenant on the same memory.db (cached)."""
        if tenant_id == self.store.tenant:
            return self
        cache: dict[str, Any] = self.__dict__.setdefault("_tenant_services", {})
        if tenant_id not in cache:
            store = MemoryStore(self.store.path, self.store.dims, tenant=tenant_id)
            factory: Any = type(self)
            cache[tenant_id] = factory(self.s, store)
        return cache[tenant_id]

    async def for_agent(self, manifest: Any) -> Any:
        """M7: the service for an agent's `memory_tenant` (`agent:<slug>`, provisioned on first
        use), or `self` when `m7.agents.memory_tenants` is off or the agent names none."""
        slug = getattr(manifest, "memory_tenant", None)
        if not slug or not self.s.settings.m7.agents.memory_tenants:
            return self
        tenant_id = f"{AGENT_NS}{slug}"
        cache: dict[str, Any] = self.__dict__.setdefault("_tenant_services", {})
        if tenant_id not in cache:
            await self.provision_tenant(tenant_id, kind="agent")
            # opened in the worker thread: no sqlite connection is made on the event loop
            store = await self._run(MemoryStore, self.store.path, self.store.dims, tenant_id)
            if tenant_id in cache:  # a concurrent first turn won the race
                await self._run(store.close)
            else:
                factory: Any = type(self)
                cache[tenant_id] = factory(self.s, store)
        return cache[tenant_id]

    def close_tenants(self) -> None:
        for svc in self.__dict__.pop("_tenant_services", {}).values():
            svc.close()


__all__ = [
    "AGENT_NS",
    "MEMORY_TAG",
    "SCOPES",
    "TENANT_DENY",
    "TYPES",
    "I4ServiceMixin",
    "TenantDenied",
    "decay",
    "visible",
]
