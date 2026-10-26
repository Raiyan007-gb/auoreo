"""Run one tool call through the full pipeline (spec §9.4–9.6).

availability → lossless parse → validate (JSON Schema, typed `InvalidArgumentsError`) →
PreToolUse hooks (deny / modify → re-validate) →
interactive gate → tool `pre_check` floor →
permission (floors, rules, mode, smart-mode guardian, human approval)
→ scrubbed env → handler with timeout (per-path locks for writers, sorted; `ctx.progress()`)
→ `output_schema` → redact → prune/spill (capped) → audit → PostToolUse hooks.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.backends.sandbox import escalation_request
from auoreo.core import observability as obs
from auoreo.core.guardian import guardian_verdict
from auoreo.core.redaction import redact_tool_output
from auoreo.core.secrets import child_env, secret_scope
from auoreo.gateway.types import ToolCall
from auoreo.harness.loader import LoadError, ToolDef
from auoreo.runtime.hooks import RESULT_PREVIEW_CHARS, hooks_of
from auoreo.tools import gates_literal, shell_floor
from auoreo.tools.args import (
    InvalidArgumentsError,
    format_validation_error,
    snapshot_json_value,
    validate_args,
)
from auoreo.tools.context import (
    CURRENT_CALL,
    CURRENT_EXEC,
    CURRENT_SESSION,
    CallInfo,
    ExecPolicy,
    ToolContext,
    ToolError,
    ToolResult,
)
from auoreo.tools.gates import external_dir_gate, risk_tier, secret_names, unattended_gate
from auoreo.tools.locks import PathLocks, touched_paths
from auoreo.tools.stages import agent_of as _agent
from auoreo.tools.stages import check_output, prune
from auoreo.tools.stages import detail as _detail
from auoreo.tools.stages import policy_context as _policy_context
from auoreo.tools.stages import pre_check as _pre_check

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


__all__ = ["ToolExecutor", "ToolOutcome", "format_validation_error", "prune"]


class ToolExecutor:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.path_locks = PathLocks()

    def _unavailable(self, tool: ToolDef) -> str | None:
        availability = getattr(getattr(self.s, "harness", None), "availability", None)
        return availability.reason(tool) if availability is not None else None

    def _pattern(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> str:
        flag = next((f for f in tool.manifest.pattern_flags if args.get(f) is True), None)
        if flag is not None:  # I3: e.g. cronjob dry_run=true → pattern `dry_run`
            return flag
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

        if (why := self._unavailable(tool)) is not None:
            return self._err(call, f"tool '{call.name}' is unavailable: {why}")

        # Stage 1: lossless shape. `args` is dispatched; `logged` is what hooks/audit see.
        try:
            snap = snapshot_json_value(tool.name, call.arguments)
            args, logged = snap.dispatched, snap.logged
            validate_args(tool.name, args, tool.manifest.parameters)
        except InvalidArgumentsError as exc:
            return self._err(call, str(exc))

        hooks = hooks_of(self.s)
        if hooks is not None:
            pre = await hooks.run(
                "PreToolUse",
                session_id=ctx.session_id,
                payload={"agent": _agent(ctx), "tool": tool.name, "arguments": logged},
                match_key=tool.name,
            )
            if pre.denied:
                return self._err(call, f"blocked by a PreToolUse hook: {pre.message}")
            if pre.input is not None:  # modified arguments must pass the schema again
                args, logged = pre.input, copy.deepcopy(pre.input)
                try:
                    validate_args(tool.name, args, tool.manifest.parameters)
                except InvalidArgumentsError as exc:  # not the model's fault: never refunded
                    return self._err(call, f"a PreToolUse hook produced {exc.detail}")

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

        # Shell text: oversized payloads are saved, never run; floors for non-bash shells.
        limit = self.s.settings.floors.max_inline_command_bytes
        if shell := shell_floor.check(tool, args, ctx, engine, home=self.s.settings.home, limit=limit):
            await self.s.audit.record(
                actor="agent",
                action="tool.denied",
                session_id=ctx.session_id,
                decision="deny",
                reason=shell[0],
                detail={"tool": tool.name, "call_id": call.id, "why": shell[1][:300]},
            )
            return self._err(call, shell[1])

        # Stage 2: permission (a file path outside cwd first passes `external_directory`).
        if (refused := await external_dir_gate(self, call, tool, args, ctx, engine)) is not None:
            return refused
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
        literal = gates_literal.scan(self, tool, args)  # --- H1b: literal secrets -> masked, human-only
        if literal is not None:
            pattern = gates_literal.mask_pattern(pattern, literal, tool, args)
            audit["pattern"] = pattern[:500]
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
        if literal is not None:  # always a human, one-shot, masked card (§25.10)
            if refused := await gates_literal.gate(
                self, call, tool, ctx, permission, pattern, literal, audit
            ):
                return refused
            reason = f"floor: {gates_literal.NOTE}"
        elif decision.action == "ask":
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
        ctx.services.setdefault("executor", self)  # `tool_call` re-enters the pipeline
        policy = ExecPolicy(mode=mode, net=tool.manifest.net, grant_id=grant_id)
        engine_cfg = self.s.settings.tool_engine
        info = CallInfo(
            call.id,
            tool.name,
            engine_cfg.progress_interval_ms / 1000,
            progress_schema=tool.manifest.progress_schema,
        )
        try:
            handler = tool.handler()
            session_token = CURRENT_SESSION.set(ctx.session_id)
            exec_token = CURRENT_EXEC.set(policy)
            call_token = CURRENT_CALL.set(info)
            try:
                with secret_scope(tool.name, secret_names(tool, self.s.settings, self.s.harness)):
                    async with self.path_locks.hold(touched_paths(tool, args, ctx)):
                        coro = obs.traced(
                            handler(args, ctx), "tool.call", tool=tool.name, decision=decision.action
                        )
                        raw = await asyncio.wait_for(coro, tool.manifest.timeout_ms / 1000)
            finally:
                CURRENT_CALL.reset(call_token)
                CURRENT_EXEC.reset(exec_token)
                CURRENT_SESSION.reset(session_token)
            result = check_output(tool, raw if isinstance(raw, ToolResult) else ToolResult(str(raw)))
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

        text = redact_tool_output(
            result.content,
            mode=tool.manifest.redaction,
            permission=tool.manifest.permission_name,
            settings=self.s.settings,
        )
        limit = min(tool.manifest.max_result_size_chars, self.s.settings.loop.tool_result_inline_chars)
        spill = self.s.settings.home / "sessions" / ctx.session_id / "tool-results"
        text, prune_meta = prune(text, limit, spill, call.id, engine_cfg.spill_max_bytes)
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
                    "arguments": logged,
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
            refused = await unattended_gate(self, call, tool, ctx, permission, pattern, audit, reason)
            return refused, (reason if refused is not None else f"{reason}+unattended")
        outcome = await self.s.approvals.request(
            ctx.session_id,
            tool.name,
            permission,
            pattern,
            _detail(tool, args),
            cwd=str(ctx.cwd),
            risk=risk_tier(tool),
            one_shot=not sticky,
        )
        await self.s.audit.record(
            actor="human",
            action="permission.reply",
            session_id=ctx.session_id,
            decision=outcome.reply or "timeout",
            reason=reason,
            detail={**audit, "request_id": outcome.request_id},
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
        if decision.action == "ask" or (
            decision.reason == "mode" and decision.note == "yolo"
        ):  # yolo never grants a wider mode
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
