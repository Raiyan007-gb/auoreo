"""Lifecycle hooks (spec §2.11, §8.5): `hooks/<name>.yaml` commands run at engine seams.

`HookRunner.run(event, ...)` selects the enabled hooks for `event` whose `matcher`
(fnmatch, `a|b` alternatives) matches `match_key`, runs each `command` without a shell
(cwd = session cwd, scrubbed env + `AUOREO_SESSION_ID`/`AUOREO_HOOK_EVENT`, JSON payload on
stdin) and folds the stdout replies into one `HookOutcome`:

- tool events (`PreToolUse`/`PostToolUse`): allow | deny{message} | modify{input}
  (modifications chain; the first deny wins);
- `PermissionRequest` (fired on the `ask` path before a human is asked): an explicit
  allow grants this one call, deny{message} refuses it, no reply / `{}` defers to the human;
- `UserPromptSubmit`: modify{text} | deny{message};
- `Stop`: {block, reason} (the first block wins);
- `SubagentStop` (a delegated child run ended; matcher = child agent): informational;
- everything else is informational.

A crash, non-zero exit, timeout or invalid JSON follows the hook's `on_error`: `warn`
logs and continues, `block` denies (tool/prompt events) or blocks (`Stop`). Hooks never
recurse: nothing runs while a hook is running in this task, nor inside a process that was
itself started by a hook (`AUOREO_HOOK_EVENT` set).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import fnmatch
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from auoreo.core.secrets import child_env
from auoreo.harness.manifests import HookEvent, HookManifest

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.harness.loader import Loaded

log = logging.getLogger(__name__)

Decision = Literal["allow", "deny", "modify", "block"]
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PermissionRequest"})
RESULT_PREVIEW_CHARS = 8000
_IN_HOOK: contextvars.ContextVar[bool] = contextvars.ContextVar("auoreo_in_hook", default=False)


@dataclass
class HookOutcome:
    decision: Decision = "allow"
    message: str = ""
    input: dict[str, Any] | None = None
    """Replacement tool arguments (`modify` on tool events)."""
    text: str | None = None
    """Replacement prompt text (`modify` on `UserPromptSubmit`)."""
    ran: list[str] = field(default_factory=list)
    granted: bool = False
    """`PermissionRequest`: a hook explicitly answered `allow` (and none denied)."""

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def blocked(self) -> bool:
        return self.decision == "block"


class HookError(Exception):
    pass


def matches(matcher: str, key: str) -> bool:
    return any(fnmatch.fnmatchcase(key, part.strip()) for part in matcher.split("|") if part.strip())


# ------------------------------------------------------------------ reply folding
def _fold_tool(out: HookOutcome, reply: dict[str, Any], name: str) -> bool:
    decision = reply.get("decision", "allow")
    if decision == "deny":
        out.decision, out.message = "deny", str(reply.get("message") or f"denied by hook '{name}'")
        return True
    if decision == "modify":
        new = reply.get("input")
        if not isinstance(new, dict):
            raise HookError("decision 'modify' needs an object 'input'")
        out.decision, out.input = "modify", new
    elif decision != "allow":
        raise HookError(f"unknown decision {decision!r}")
    return False


def _fold_permission(out: HookOutcome, reply: dict[str, Any], name: str) -> bool:
    decision = reply.get("decision")
    if decision == "deny":
        out.granted = False
        out.decision, out.message = "deny", str(reply.get("message") or f"denied by hook '{name}'")
        return True
    if decision == "allow":
        out.granted = True
    elif decision is not None:
        raise HookError(f"unknown decision {decision!r} (PermissionRequest takes allow | deny)")
    return False


def _fold_prompt(out: HookOutcome, reply: dict[str, Any], name: str) -> bool:
    decision = reply.get("decision", "allow")
    if decision == "deny":
        out.decision, out.message = "deny", str(reply.get("message") or f"prompt blocked by hook '{name}'")
        return True
    if decision == "modify":
        text = reply.get("text")
        if not isinstance(text, str):
            raise HookError("decision 'modify' needs a string 'text'")
        out.decision, out.text = "modify", text
    elif decision != "allow":
        raise HookError(f"unknown decision {decision!r}")
    return False


def _fold_stop(out: HookOutcome, reply: dict[str, Any], name: str) -> bool:
    if reply.get("block") is True:
        out.decision, out.message = "block", str(reply.get("reason") or f"stop blocked by hook '{name}'")
        return True
    return False


def _fold_info(out: HookOutcome, reply: dict[str, Any], name: str) -> bool:
    return False


_FOLD = {
    "PreToolUse": _fold_tool,
    "PostToolUse": _fold_tool,
    "PermissionRequest": _fold_permission,
    "UserPromptSubmit": _fold_prompt,
    "Stop": _fold_stop,
}
_ERROR_DECISION: dict[str, Decision] = {
    "PreToolUse": "deny",
    "PostToolUse": "deny",
    "PermissionRequest": "deny",
    "UserPromptSubmit": "deny",
    "Stop": "block",
}


class HookRunner:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._sub: Any = None
        self._watch: asyncio.Task[None] | None = None
        self._agents: dict[str, str] = {}

    # ------------------------------------------------------------- selection
    def select(self, event: HookEvent, match_key: str) -> list[Loaded]:
        hooks = sorted(self.s.harness.hooks.values(), key=lambda h: h.manifest.name)
        return [
            h
            for h in hooks
            if h.manifest.enabled and h.manifest.event == event and matches(h.manifest.matcher, match_key)
        ]

    async def run(
        self, event: HookEvent, *, session_id: str, payload: dict[str, Any], match_key: str = "*"
    ) -> HookOutcome:
        out = HookOutcome()
        if _IN_HOOK.get() or os.environ.get("AUOREO_HOOK_EVENT"):
            return out  # never recursively
        selected = self.select(event, match_key)
        if not selected:
            return out
        body = {**payload, "session_id": session_id, "event": event}
        fold = _FOLD.get(event, _fold_info)
        token = _IN_HOOK.set(True)
        try:
            for hook in selected:
                if out.input is not None:
                    body["arguments"] = out.input
                if out.text is not None:
                    body["text"] = out.text
                stop = await self._one(hook, event, session_id, body, out, fold)
                if stop:
                    break
        finally:
            _IN_HOOK.reset(token)
        if out.denied or out.blocked:
            await self.s.audit.record(
                actor="hook",
                action=f"hook.{out.decision}",
                session_id=session_id,
                decision=out.decision,
                reason=f"hook:{event}",
                detail={
                    "event": event,
                    "hooks": out.ran,
                    "match_key": match_key,
                    "message": out.message[:500],
                },
            )
        return out

    async def _one(
        self,
        hook: Loaded,
        event: HookEvent,
        session_id: str,
        body: dict[str, Any],
        out: HookOutcome,
        fold: Any,
    ) -> bool:
        m: HookManifest = hook.manifest
        t0 = time.monotonic()
        before = out.decision
        stop = False
        try:
            reply = await self._exec(hook, event, session_id, body)
            stop = fold(out, reply, m.name)
            decision: str = out.decision if out.decision != before or stop else "allow"
        except HookError as exc:
            decision = "error"
            if m.on_error == "block" and event in _ERROR_DECISION:
                out.decision = _ERROR_DECISION[event]
                out.message = f"hook '{m.name}' failed ({exc}); blocked by on_error: block"
                decision, stop = out.decision, True
            else:
                log.warning("hook %s (%s) failed: %s", m.name, event, exc)
        out.ran.append(m.name)
        ms = int((time.monotonic() - t0) * 1000)
        await self.s.emit(
            session_id, "hook.ran", {"name": m.name, "event": event, "decision": decision, "ms": ms}
        )
        return stop

    def _argv(self, hook: Loaded) -> list[str]:
        """`./x` / `../x` resolve against the definition root when that file exists there
        (so bundled and overlay hooks can ship their scripts); otherwise against the cwd."""
        root = Path(hook.path).parent.parent
        argv = []
        for arg in hook.manifest.command:
            if arg.startswith(("./", "../")) and (root / arg).exists():
                arg = str((root / arg).resolve())
            argv.append(arg)
        return argv

    async def _exec(
        self, hook: Loaded, event: HookEvent, session_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        m: HookManifest = hook.manifest
        env = child_env([], self.s.secrets)
        env.update(AUOREO_SESSION_ID=session_id, AUOREO_HOOK_EVENT=event, AUOREO_HOOK_NAME=m.name)
        data = json.dumps(body, default=str).encode()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv(hook),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.s.cwd),
                env=env,
            )
        except OSError as exc:
            raise HookError(f"cannot start: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(data), m.timeout_ms / 1000)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise HookError(f"timed out after {m.timeout_ms} ms") from None
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", "replace").strip()[-300:]
            raise HookError(f"exit code {proc.returncode}" + (f": {tail}" if tail else ""))
        text = stdout.decode("utf-8", "replace").strip()
        if not text:
            return {}
        try:
            reply = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HookError(f"stdout is not JSON: {exc.msg}") from exc
        if not isinstance(reply, dict):
            raise HookError("stdout JSON must be an object")
        return reply

    # ------------------------------------------------------- session lifecycle
    def remember_agent(self, session_id: str, agent: str) -> None:
        self._agents[session_id] = agent
        self.ensure_watch()

    async def session_end(self, session_id: str, reason: str) -> None:
        """Best effort: SessionEnd for an archived/deleted session."""
        agent = self._agents.pop(session_id, None)
        if agent is None:
            row = await self.s.get_session(session_id)
            agent = row.agent if row is not None else "*"
        try:
            await self.run(
                "SessionEnd",
                session_id=session_id,
                payload={"agent": agent, "reason": reason},
                match_key=agent,
            )
        except Exception:  # lifecycle hooks must never break the server
            log.exception("SessionEnd hooks failed for %s", session_id)

    async def _watch_sessions(self) -> None:
        while True:
            if self._sub is None or self._sub.overflowed:
                if self._sub is not None:
                    self.s.bus.unsubscribe(self._sub)
                self._sub = self.s.bus.subscribe(None)
            ev = await self._sub.queue.get()
            if ev.type == "session.archived":
                await self.session_end(ev.aggregate_id, "archived")
            elif ev.type == "session.deleted" and isinstance(ev.data.get("session_id"), str):
                await self.session_end(ev.data["session_id"], "deleted")

    def ensure_watch(self) -> None:
        """Watch the bus for archive/delete only once a SessionEnd hook exists (no idle
        subscriber otherwise)."""
        if self._watch is not None:
            return
        if not any(
            h.manifest.enabled and h.manifest.event == "SessionEnd" for h in self.s.harness.hooks.values()
        ):
            return
        self._sub = self.s.bus.subscribe(None)
        self._watch = asyncio.create_task(self._watch_sessions(), name="hooks:sessions")

    async def close(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watch
        if self._sub is not None:
            self.s.bus.unsubscribe(self._sub)


def hooks_of(services: Any) -> HookRunner | None:
    ext = getattr(services, "ext", None) or {}
    runner = ext.get("hooks")
    return runner if isinstance(runner, HookRunner) else None


async def start(services: Services) -> HookRunner | None:
    if not services.settings.hooks_enabled:
        return None
    runner = HookRunner(services)
    runner.ensure_watch()
    return runner


async def subagent_stop(services: Any, data: dict[str, Any]) -> None:
    """Fire `SubagentStop` (informational; matcher = child agent name) when a delegated child
    run ends, on the parent session. Never raises into the caller."""
    hooks = hooks_of(services)
    if hooks is None:
        return
    try:
        await hooks.run("SubagentStop", session_id=data["parent"], payload=data, match_key=data["agent"])
    except Exception:
        log.exception("SubagentStop hooks failed for %s", data.get("child"))
