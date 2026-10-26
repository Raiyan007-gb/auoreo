"""User preference learning (spec §11, P1): the agent *proposes* PREFS.md changes; a human
approves or denies them; only an approved proposal is applied.

- Proposals live in `<home>/memory/.prefs_proposals.json` (status `pending` → `approved` |
  `denied`); the agent never writes `PREFS.md` itself (`memory` tool `target: prefs`).
- Approving emits `prefs.approved`; that event's id is recorded in the proposal and in the
  applied PREFS.md entry: `- <content> <!-- since 2026-10-26 · approval evt:<id> · <proposal id> -->`.
- `add` appends an entry; `replace`/`remove` need `old_text` matching exactly one entry line,
  checked again at approval time (the file may have changed since the proposal).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from auoreo.core.redaction import PREFIXES
from auoreo.memory.files import memory_dir

Action = Literal["add", "replace", "remove"]
Status = Literal["pending", "approved", "denied"]
ACTIONS: frozenset[str] = frozenset({"add", "replace", "remove"})
PREFS_FILE = "PREFS.md"
STORE = ".prefs_proposals.json"
MAX_CONTENT = 500
_lock = threading.Lock()


class PrefsError(ValueError):
    """Invalid proposal or state transition (API: 404/409/422)."""


class ProposalNotFound(PrefsError):
    pass


@dataclass
class Proposal:
    id: str
    action: str
    content: str
    old_text: str | None = None
    reason: str = ""
    session_id: str | None = None
    actor: str = "agent"
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None
    approval_event_id: int | None = None
    deny_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def looks_secret(text: str) -> bool:
    return any(p in text for p in PREFIXES if len(p) > 3) or "PRIVATE KEY" in text


class PrefsInbox:
    def __init__(self, home: Path, max_pending: int = 50) -> None:
        self.home = home
        self.max_pending = max_pending

    @property
    def path(self) -> Path:
        return memory_dir(self.home) / STORE

    @property
    def prefs_path(self) -> Path:
        return memory_dir(self.home) / PREFS_FILE

    # ------------------------------------------------------------ storage
    def _load(self) -> list[Proposal]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [Proposal(**p) for p in raw if isinstance(p, dict)]

    def _save(self, items: list[Proposal]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([p.to_dict() for p in items], indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------- public
    def proposals(self, status: str | None = None) -> list[Proposal]:
        with _lock:
            items = self._load()
        out = [p for p in items if status is None or p.status == status]
        return sorted(out, key=lambda p: p.created_at, reverse=True)

    def get(self, pid: str) -> Proposal:
        for p in self.proposals():
            if p.id == pid:
                return p
        raise ProposalNotFound(f"unknown preference proposal '{pid}'")

    def validate(self, action: str, content: str, old_text: str | None) -> None:
        if action not in ACTIONS:
            raise PrefsError(f"action must be one of {sorted(ACTIONS)}")
        content = content.strip()
        if action in {"add", "replace"} and not content:
            raise PrefsError(f"{action} needs content")
        if len(content) > MAX_CONTENT or "\n" in content:
            raise PrefsError(f"a preference is one line of at most {MAX_CONTENT} chars")
        if looks_secret(content) or looks_secret(old_text or ""):
            raise PrefsError("refusing to store something that looks like a secret")
        if action in {"replace", "remove"}:
            if not old_text:
                raise PrefsError(f"{action} needs old_text")
            self._match(old_text)

    def propose(
        self,
        action: str,
        content: str = "",
        *,
        old_text: str | None = None,
        reason: str = "",
        session_id: str | None = None,
        actor: str = "agent",
    ) -> Proposal:
        self.validate(action, content, old_text)
        with _lock:
            items = self._load()
            dup = next(
                (
                    p
                    for p in items
                    if p.status == "pending"
                    and (p.action, p.content, p.old_text) == (action, content.strip(), old_text)
                ),
                None,
            )
            if dup is not None:
                return dup
            prop = Proposal(
                id=f"pref_{uuid.uuid4().hex[:12]}",
                action=action,
                content=content.strip(),
                old_text=old_text,
                reason=reason.strip()[:500],
                session_id=session_id,
                actor=actor,
            )
            items.append(prop)
            pending = sorted((p for p in items if p.status == "pending"), key=lambda p: p.created_at)
            for old in pending[: max(0, len(pending) - self.max_pending)]:
                old.status, old.decided_at, old.deny_reason = "denied", time.time(), "expired (inbox full)"
            self._save(items)
        return prop

    def approve(self, pid: str, *, approval_event_id: int, by: str = "user") -> Proposal:
        """Apply a pending proposal to PREFS.md and record the approval event id."""
        with _lock:
            items = self._load()
            prop = self._pending(items, pid)
            text = (
                self.prefs_path.read_text(encoding="utf-8")
                if self.prefs_path.is_file()
                else "# Preferences\n"
            )
            self.prefs_path.write_text(self._apply(text, prop, approval_event_id), encoding="utf-8")
            prop.status, prop.decided_at, prop.decided_by = "approved", time.time(), by
            prop.approval_event_id = approval_event_id
            self._save(items)
        return prop

    def deny(self, pid: str, *, reason: str = "", by: str = "user") -> Proposal:
        with _lock:
            items = self._load()
            prop = self._pending(items, pid)
            prop.status, prop.decided_at, prop.decided_by = "denied", time.time(), by
            prop.deny_reason = reason.strip()[:500] or None
            self._save(items)
        return prop

    def check_applicable(self, pid: str) -> Proposal:
        """Raise if the proposal is not pending or no longer applies to the current file."""
        prop = self.get(pid)
        if prop.status != "pending":
            raise PrefsError(f"proposal {pid} is already {prop.status}")
        if prop.action in {"replace", "remove"}:
            self._match(prop.old_text or "")
        return prop

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _pending(items: list[Proposal], pid: str) -> Proposal:
        prop = next((p for p in items if p.id == pid), None)
        if prop is None:
            raise ProposalNotFound(f"unknown preference proposal '{pid}'")
        if prop.status != "pending":
            raise PrefsError(f"proposal {pid} is already {prop.status}")
        return prop

    def _match(self, old_text: str) -> int:
        lines = self._lines()
        hits = [i for i, line in enumerate(lines) if line.startswith("- ") and old_text in line]
        if len(hits) != 1:
            raise PrefsError(f"old_text matches {len(hits)} PREFS.md entries; it must match exactly one")
        return hits[0]

    def _lines(self) -> list[str]:
        if not self.prefs_path.is_file():
            return []
        return self.prefs_path.read_text(encoding="utf-8").splitlines()

    def _apply(self, text: str, prop: Proposal, event_id: int) -> str:
        since = datetime.now(UTC).date().isoformat()
        entry = f"- {prop.content} <!-- since {since} · approval evt:{event_id} · {prop.id} -->"
        if prop.action == "add":
            return text.rstrip("\n") + "\n" + entry + "\n"
        lines = text.splitlines()
        idx = self._match(prop.old_text or "")
        if prop.action == "replace":
            lines[idx] = entry
        else:
            del lines[idx]
        return "\n".join(lines) + "\n"


def inbox(settings: Any) -> PrefsInbox:
    g6 = getattr(settings, "g6", None)
    return PrefsInbox(settings.home, g6.prefs.max_pending if g6 is not None else 50)
