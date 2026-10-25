"""Run one tool call through the full pipeline (spec §9.4–9.6).

parse → validate (JSON Schema) → PreToolUse hooks (deny / modify → re-validate) →
interactive gate → tool `pre_check` floor →
permission (floors, rules, mode, smart-mode guardian, human approval)
→ scrubbed env → handler with timeout (per-path lock for writers)
→ redact → prune/spill → audit → PostToolUse hooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema

from auoreo.backends.sandbox import escalation_request
from auoreo.core import observability as obs
from auoreo.core.guardian import guardian_verdict
from auoreo.core.redaction import redact
from auoreo.core.secrets import child_env
from auoreo.gateway.types import ToolCall
from auoreo.harness.loader import LoadError, ToolDef
from auoreo.runtime.hooks import RESULT_PREVIEW_CHARS, hooks_of
from auoreo.tools.context import (
    CURRENT_EXEC,
    CURRENT_SESSION,
    ExecPolicy,
    ToolContext,
    ToolError,
    ToolResult,
)

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.core.services import Services

log = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    call: ToolCall
    result: ToolResult
    halt: bool = False  # a bare reject stops the turn (§8.4)
    meta: dict[str, Any] = field(default_factory=dict)


def format_validation_error(err: jsonschema.ValidationError) -> str:
    where = "/".join(str(p) for p in err.absolute_path) or "(root)"
    return f"invalid arguments at {where}: {err.message}"


def prune(text: str, limit: int, spill_dir: Path, call_id: str) -> tuple[str, dict[str, Any]]:
    """Keep results under `limit` chars; spill the full text to disk (spec §9.3)."""
    if len(text) <= limit:
        return text, {}
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{call_id}.txt"
    path.write_text(text, encoding="utf-8")
    head = text[: limit * 3 // 4]
    tail = text[-limit // 8 :]
    note = f"\n… [{len(text) - len(head) - len(tail)} chars truncated; full output: {path}] …\n"
    return head + note + tail, {"truncated": True, "output_path": str(path)}


class ToolExecutor:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._path_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> asyncio.Lock | None:
        arg = tool.manifest.pattern_arg
        if tool.manifest.is_read_only or not arg or arg != "path" or not isinstance(args.get(arg), str):
            return None
        key = str(ctx.resolve_path(args[arg]))
        return self._path_locks.setdefault(key, asyncio.Lock())

    def _pattern(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> str:
        arg = tool.manifest.pattern_arg
        if not arg or not isinstance(args.get(arg), str):
            return "*"
        value = args[arg]
        if arg == "path":
            return str(ctx.resolve_path(value))
        return str(value)

    async def run(
        self,
        call: ToolCall,
        *,
        tools: dict[str, ToolDef],
        ctx: ToolContext,
        engine: PermissionEngine,
    ) -> ToolOutcome:
        tool = tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(tools)) or "none"
            return self._err(call, f"unknown tool '{call.name}'. Available: {known}")

        # Stage 1: shape.
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return self._err(call, f"arguments are not valid JSON: {exc.msg}")
        if not isinstance(args, dict):
            return self._err(call, "arguments must be a JSON object")
        try:
            jsonschema.validate(args, tool.manifest.parameters)
        except jsonschema.ValidationError as exc:
            return self._err(call, format_validation_error(exc))

        hooks = hooks_of(self.s)
        if hooks is not None:
            pre = await hooks.run(
                "PreToolUse",
                session_id=ctx.session_id,
                payload={"agent": _agent(ctx), "tool": tool.name, "arguments": args},
                match_key=tool.name,
            )
            if pre.denied:
                return self._err(call, f"blocked by a PreToolUse hook: {pre.message}")
            if pre.input is not None:  # modified arguments must pass the schema again
                args = pre.input
                try:
                    jsonschema.validate(args, tool.manifest.parameters)
                except jsonschema.ValidationError as exc:
                    return self._err(call, f"a PreToolUse hook produced {format_validation_error(exc)}")

        if tool.manifest.interactive_only and not ctx.interactive:
            return self._err(call, f"'{tool.name}' needs a human and this run is non-interactive")

        # Sandbox escalation fields are validated before any prompt (spec §24.9).
        mode, wider, why = self._sandbox_args(tool, args)
        if why is not None:
            return self._err(call, why)

        # Tool-specific floor before any prompt (F5): `pre_check(args) -> str | None` in the
        # handler module. A refusal is final: no rule, mode or approval can override it.
        if (why := _pre_check(tool, args)) is not None:
            await self.s.audit.record(
                actor="agent",
                action="tool.denied",
                session_id=ctx.session_id,
                decision="deny",
                reason="pre_check",
                detail={"tool": tool.name, "call_id": call.id, "why": why[:300]},
            )
            return self._err(call, f"refused: {why}. Do not retry unchanged.")

        # Stage 2: permission.
        permission = tool.manifest.permission_name
        pattern = self._pattern(tool, args, ctx)
        decision = engine.evaluate(
            permission,
            pattern,
            tool.manifest.default_action,
            interactive=ctx.interactive,
            context=_policy_context(tool, ctx),
        )
        audit = {
            "tool": tool.name,
            "permission": permission,
            "pattern": pattern[:500],
            "call_id": call.id,
            "net": tool.manifest.net,
        }
        reason = decision.detail
        if decision.action == "deny":
            await self.s.audit.record(
                actor="agent",
                action="tool.denied",
                session_id=ctx.session_id,
                decision="deny",
                reason=decision.detail,
                detail=audit,
            )
            return self._err(call, f"permission denied ({decision.detail}). Do not retry unchanged.")
        if decision.action == "ask":
            refused, reason = await self._ask(
                call, tool, args, ctx, engine, permission, pattern, audit, reason
            )
            if refused is not None:
                return refused
        grant_id: str | None = None
        if wider is not None:
            refused, grant_id = await self._escalate(call, tool, args, ctx, engine, mode, wider, audit)
            if refused is not None:
                return refused
            mode = wider

        # Execute.
        ctx.env = child_env(tool.manifest.requires_env, self.s.secrets)
        ctx.services.setdefault("services", self.s)
        policy = ExecPolicy(mode=mode, net=tool.manifest.net, grant_id=grant_id)
        lock = self._lock_for(tool, args, ctx)
        try:
            handler = tool.handler()
            session_token = CURRENT_SESSION.set(ctx.session_id)
            exec_token = CURRENT_EXEC.set(policy)
            try:
                coro = obs.traced(handler(args, ctx), "tool.call", tool=tool.name, decision=decision.action)
                if lock is not None:
                    async with lock:
                        raw = await asyncio.wait_for(coro, tool.manifest.timeout_ms / 1000)
                else:
                    raw = await asyncio.wait_for(coro, tool.manifest.timeout_ms / 1000)
            finally:
                CURRENT_EXEC.reset(exec_token)
                CURRENT_SESSION.reset(session_token)
            result = raw if isinstance(raw, ToolResult) else ToolResult(str(raw))
        except ToolError as exc:
            result = ToolResult(str(exc), is_error=True)
        except TimeoutError:
            result = ToolResult(f"tool timed out after {tool.manifest.timeout_ms} ms", is_error=True)
        except LoadError as exc:
            result = ToolResult(f"tool failed to load: {exc.message}", is_error=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a tool bug must not kill the turn
            log.exception("tool %s crashed", tool.name)
            result = ToolResult(f"tool crashed: {type(exc).__name__}: {exc}", is_error=True)

        text = redact(result.content) if self.s.settings.redact_secrets else result.content
        limit = min(tool.manifest.max_result_size_chars, self.s.settings.loop.tool_result_inline_chars)
        spill = self.s.settings.home / "sessions" / ctx.session_id / "tool-results"
        text, prune_meta = prune(text, limit, spill, call.id)
        result = ToolResult(text, result.is_error, {**result.meta, **prune_meta}, images=result.images)
        await self.s.audit.record(
            actor="agent",
            action="tool.call",
            session_id=ctx.session_id,
            decision="allow",
            reason=reason,
            detail={**audit, "is_error": result.is_error},
        )
        if hooks is not None:
            await hooks.run(
                "PostToolUse",
                session_id=ctx.session_id,
                payload={
                    "agent": _agent(ctx),
                    "tool": tool.name,
                    "arguments": args,
                    "result": result.content[:RESULT_PREVIEW_CHARS],
                    "is_error": result.is_error,
                },
                match_key=tool.name,
            )
        return ToolOutcome(call, result)

    async def _ask(
        self,
        call: ToolCall,
        tool: ToolDef,
        args: dict[str, Any],
        ctx: ToolContext,
        engine: PermissionEngine,
        permission: str,
        pattern: str,
        audit: dict[str, Any],
        reason: str,
        *,
        sticky: bool = True,
    ) -> tuple[ToolOutcome | None, str]:
        """The `ask` path: guardian (smart mode, or a policy `review`), then
        `PermissionRequest` hooks, then the human. Returns `(error outcome or None to
        proceed, audit reason)`."""
        review = reason.startswith("policy_review:")
        if engine.mode == "smart" or review:
            spec = ctx.services.get("spec")
            verdict = await guardian_verdict(
                self.s,
                tool=tool.name,
                permission=permission,
                pattern=pattern,
                detail=_detail(tool, args),
                session_id=ctx.session_id,
                model=getattr(spec, "model", None),
            )
            who = f"{reason}+guardian" if review else "guardian"
            if verdict == "APPROVE":
                return None, who
            if verdict == "DENY":
                kind = "policy-review" if review else "smart-mode"
                msg = f"denied by the {kind} guardian for `{pattern[:200]}`. Do not retry unchanged."
                return self._err(call, msg), who
        hooks = hooks_of(self.s)
        if hooks is not None:
            hook = await hooks.run(
                "PermissionRequest",
                session_id=ctx.session_id,
                payload={
                    "agent": _agent(ctx),
                    "tool": tool.name,
                    "arguments": args,
                    "permission": permission,
                    "pattern": pattern,
                    "reason": reason,
                },
                match_key=tool.name,
            )
            if hook.denied:
                return self._err(call, f"blocked by a PermissionRequest hook: {hook.message}"), "hook"
            if hook.granted:
                return None, "hook:PermissionRequest"
        if not ctx.interactive:
            return self._err(
                call,
                f"'{tool.name}' needs approval for `{pattern[:200]}` but this "
                "run is non-interactive; allow it with a permission rule",
            ), reason
        outcome = await self.s.approvals.request(
            ctx.session_id, tool.name, permission, pattern, _detail(tool, args)
        )
        await self.s.audit.record(
            actor="human",
            action="permission.reply",
            session_id=ctx.session_id,
            decision=outcome.reply or "timeout",
            reason=reason,
            detail=audit,
        )
        if not outcome.granted:
            if outcome.message:
                return self._err(call, f"user rejected this call with feedback: {outcome.message}"), reason
            rejected = ToolResult("user rejected this tool call", is_error=True)
            return ToolOutcome(call, rejected, halt=True), reason
        if outcome.reply == "always" and sticky:
            engine.grant_always(permission, pattern)
        return None, reason

    @staticmethod
    def _sandbox_args(tool: ToolDef, args: dict[str, Any]) -> tuple[str, str | None, str | None]:
        """`(mode, wider_mode_to_grant, error)`; only tools that declare `sandbox_mode`."""
        if "sandbox_mode" not in tool.manifest.parameters.get("properties", {}):
            return tool.manifest.sandbox_mode, None, None
        return escalation_request(tool.manifest.sandbox_mode, args)

    async def _escalate(
        self,
        call: ToolCall,
        tool: ToolDef,
        args: dict[str, Any],
        ctx: ToolContext,
        engine: PermissionEngine,
        from_mode: str,
        to_mode: str,
        audit: dict[str, Any],
    ) -> tuple[ToolOutcome | None, str | None]:
        """One-call grant for a wider sandbox mode: approval via the ask path with
        permission `sandbox_escalate` (never `always`), audited with a single-use grant id."""
        justification = str(args.get("justification", ""))
        pattern = f"{to_mode}:{justification[:60]}"
        decision = engine.evaluate(
            "sandbox_escalate",
            pattern,
            "ask",
            interactive=ctx.interactive,
            context=_policy_context(tool, ctx),
        )
        if decision.action == "deny":
            return self._err(call, f"sandbox escalation denied ({decision.detail})"), None
        if decision.action == "ask" or decision.reason == "mode: yolo":  # yolo never grants a wider mode
            refused, _ = await self._ask(
                call,
                tool,
                args,
                ctx,
                engine,
                "sandbox_escalate",
                pattern,
                audit,
                decision.detail,
                sticky=False,
            )
            if refused is not None:
                return refused, None
        grant_id = uuid.uuid4().hex
        await self.s.audit.record(
            actor="agent",
            action="sandbox.escalate",
            session_id=ctx.session_id,
            decision="allow",
            reason=decision.detail,
            detail={
                **audit,
                "fromMode": from_mode,
                "toMode": to_mode,
                "justificationHash": hashlib.sha256(justification.encode()).hexdigest()[:16],
                "grantId": grant_id,
            },
        )
        return None, grant_id

    @staticmethod
    def _err(call: ToolCall, message: str) -> ToolOutcome:
        return ToolOutcome(call, ToolResult(message, is_error=True))


def _pre_check(tool: ToolDef, args: dict[str, Any]) -> str | None:
    """The handler module's optional `pre_check(args) -> str | None` (folder tools only): a
    non-None reason refuses the call before permission evaluation, so a floor-blocked call
    never raises an approval prompt. Import failures are left to the execute stage."""
    if tool.path is None:
        return None
    try:
        module = inspect.getmodule(tool.handler())
    except LoadError:
        return None
    check = getattr(module, "pre_check", None)
    return check(args) if callable(check) else None


def _agent(ctx: ToolContext) -> str:
    spec = ctx.services.get("spec")
    return str(getattr(spec, "name", "") or "")


def _policy_context(tool: ToolDef, ctx: ToolContext) -> dict[str, Any]:
    """The per-call fields of the exec-policy `req` (spec §2.10)."""
    out: dict[str, Any] = {"tool": tool.name, "session_id": ctx.session_id, "cwd": str(ctx.cwd)}
    if agent := _agent(ctx):
        out["agent"] = agent
    return out


def _detail(tool: ToolDef, args: dict[str, Any]) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return redact(text if len(text) < 2000 else text[:2000] + "…")
