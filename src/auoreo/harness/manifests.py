"""Manifest contracts for every filesystem-defined capability.

Each kind of folder entry (agent, tool, skill, workflow, MCP server, permission file,
prompt section) is validated against one of these Pydantic models at discovery time.
Unknown keys are rejected (`extra="forbid"`) so typos fail loudly instead of being
silently ignored. Skills are the exception: they follow the Agent Skills format, which
allows vendor keys, so unknown keys are ignored there.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auoreo.harness.manifests_webhook import WebhookFilter as WebhookFilter  # re-export
from auoreo.harness.manifests_webhook import WebhookManifest as WebhookManifest  # re-export

NAME_RE = r"^[a-z][a-z0-9_]*$"
SLUG_RE = r"^[a-z0-9][a-z0-9._-]*$"

Action = Literal["allow", "ask", "deny"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolManifest(_Strict):
    """`tools/<name>/tool.yaml`. Defaults are fail-closed (spec §9.1)."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    toolset: str = "core"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    handler: str = "handler.py:run"
    is_read_only: bool = False
    is_destructive: bool = False
    is_concurrency_safe: bool = False
    is_search_or_read: bool = False
    timeout_ms: int = Field(default=120_000, ge=100, le=3_600_000)
    interrupt: Literal["block", "cancel"] = "block"
    requires_env: list[str] = Field(default_factory=list)
    permission: str | None = None
    """Permission name checked by the policy engine; defaults to the tool name."""
    pattern_arg: str | None = None
    """Argument whose value forms the permission pattern (e.g. `command`, `path`)."""
    pattern_flags: list[str] = Field(default_factory=list)
    """Boolean arguments that, when true, replace the pattern with their own name (I3:
    `cronjob` `dry_run` gets its own permission pattern, so a dry run needs no approval)."""
    shell_args: list[str] = Field(default_factory=list)
    """Arguments holding shell text (`monitor.cmd`, `write_stdin.chars`): the hardline/secret
    floors and the oversized-payload guard run on them before permission (§19.4, §19.7).
    A `bash`-permission tool's `pattern_arg` is always included."""
    default_action: Action = "ask"
    """Action used when no permission rule matches this tool."""
    exposure: Literal["direct", "deferred", "hidden"] = "direct"
    interactive_only: bool = False
    """Denied when no human can answer (non-interactive runs)."""
    search_hint: str = ""
    aliases: list[str] = Field(default_factory=list)
    max_result_size_chars: int = Field(default=64_000, ge=1_000)
    override: bool = False
    """Required to shadow a lower-precedence tool of the same name."""
    net: Literal["none", "proxy", "direct"] = "none"
    """Egress class (spec §25.10): `none` default, `proxy` (MCP/web via proxy), `direct`."""
    sandbox_mode: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    """Sandbox mode this tool's processes run under; wider needs a one-call escalation."""
    redaction: Literal["mask", "sentinel", "off"] = "mask"
    """Result redaction (spec §19.10): `mask` = display `head6…tail4`; `sentinel` (file reads)
    = `«redacted:<prefix>…»`; `off` only for tools whose output never carries file content."""
    # --- G4a: availability + presentation (spec §9.1)
    is_enabled: bool = True
    """`false` hides the tool from every agent (kept in `/api/harness` with the reason)."""
    check_fn: str | None = None
    """Availability probe `<file>.py:<fn>` (inside the tool folder) or `<pkg.module>:<fn>`:
    sync, returns True/None (available), False or a reason string (hidden). Cached 30 s."""
    env_gate: Literal["none", "all", "any"] = "none"
    """Hide the tool when `requires_env` is unmet (env or secret store): `all` names, `any`
    one of them. `none` (default) treats `requires_env` as a pass-through allowlist only."""
    emoji: str = Field(default="", max_length=8)
    output_schema: dict[str, Any] | None = None
    """JSON Schema for a structured result (`ToolResult.meta["structured"]`, else the JSON
    content); a mismatch turns the result into an error the model sees."""

    @field_validator("parameters")
    @classmethod
    def _object_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") != "object":
            raise ValueError("parameters must be a JSON Schema with type: object")
        return v

    @field_validator("handler")
    @classmethod
    def _handler_ref(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*", v) or ".." in v:
            raise ValueError("handler must look like 'handler.py:run' inside the tool folder")
        return v

    @field_validator("check_fn")
    @classmethod
    def _check_ref(cls, v: str | None) -> str | None:
        file_ref = r"[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*"
        mod_ref = r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*"
        if v is not None and (not re.fullmatch(f"{file_ref}|{mod_ref}", v) or ".." in v):
            raise ValueError("check_fn must look like 'handler.py:available' or 'pkg.module:fn'")
        return v

    @property
    def permission_name(self) -> str:
        return self.permission or self.name


class ToolSelection(_Strict):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)


class Rule(_Strict):
    """One permission rule. Evaluation is last-match-wins over ordered layers."""

    permission: str
    pattern: str = "*"
    action: Action


class AgentManifest(_Strict):
    """Frontmatter of `agents/<name>/AGENT.md`; the markdown body is the agent's prompt."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    model: str | None = None
    """Alias or routing group; `None` uses the resolution chain."""
    effort: Literal["low", "medium", "high", "max"] | None = None
    tools: ToolSelection = Field(default_factory=ToolSelection)
    skills: list[str] = Field(default_factory=lambda: ["*"])
    max_steps: int = Field(default=50, ge=1, le=500)
    permission_mode: Literal["manual", "smart", "yolo", "plan", "acceptEdits"] | None = None
    permissions: list[Rule] = Field(default_factory=list)
    memory: bool = True
    temperature: float | None = Field(default=None, ge=0, le=2)


class SkillManifest(BaseModel):
    """Frontmatter of `skills/<name>/SKILL.md` (Agent Skills format; extra keys ignored)."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    name: str = Field(pattern=SLUG_RE)
    description: str = Field(min_length=1)


LOOP_CHECKS = ("completed", "nonempty", "json")
"""Built-in `loop.until: check:<name>` predicates (never shell commands: workflows are inert)."""


class WorkflowLoop(_Strict):
    """Ralph-lite (spec §9.3): re-run the step until `until` holds or `max_iterations`."""

    until: str = Field(min_length=1)
    """A regex searched in the step output (multiline), or `check:<completed|nonempty|json>`."""
    max_iterations: int = Field(default=5, ge=1, le=50)

    @field_validator("until")
    @classmethod
    def _until(cls, v: str) -> str:
        if v.startswith("check:"):
            if v[6:] not in LOOP_CHECKS:
                raise ValueError(f"unknown check '{v[6:]}' (one of {', '.join(LOOP_CHECKS)})")
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"bad until regex: {exc}") from exc
        return v


class WorkflowStep(_Strict):
    id: str = Field(pattern=NAME_RE)
    agent: str = Field(pattern=NAME_RE)
    input: str = "{{input}}"
    """Template; `{{input}}`, `{{steps.<id>.output}}` and (looping steps)
    `{{loop.iteration}}` / `{{loop.last}}` are available."""
    loop: WorkflowLoop | None = None


class WorkflowManifest(_Strict):
    """`workflows/<name>.yaml`."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1)
    steps: list[WorkflowStep] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _unique_ids(cls, v: list[WorkflowStep]) -> list[WorkflowStep]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return v


class McpToolOverride(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    timeout_ms: int | None = Field(default=None, ge=100, le=3_600_000, alias="timeoutMs")


class McpSampling(_Strict):
    enabled: bool = False
    max_tokens: int = Field(default=1024, ge=1, le=32_000)
    """Cap applied to the server's own `maxTokens`."""
    model: str | None = None
    """Model/alias for the aux call; unset = aux routing (`auxiliary.tasks.sampling`)."""


class McpServerManifest(_Strict):
    """`mcp/<name>.yaml`. Env/header values may be `secret:<name>` references."""

    name: str = Field(pattern=NAME_RE)
    transport: Literal["stdio", "streamable-http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000)
    enabled: bool = True
    exposure: Literal["direct", "deferred"] = "deferred"
    tools: dict[str, McpToolOverride] = Field(default_factory=dict)
    """Per remote-tool overrides keyed by the server's tool name (`timeoutMs` / `timeout_ms`)."""
    sampling: McpSampling = Field(default_factory=lambda: McpSampling())
    """Server→client `sampling/createMessage` (default off; aux call, approval-gated)."""


class BackendManifest(_Strict):
    """`backends/<kind>/<name>/backend.yaml`. A backend implements one capability kind
    (capture, browser, stt, tts, …) behind the ABC in `src/auoreo/backends/<kind>.py`."""

    kind: str = Field(pattern=NAME_RE)
    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    impl: str = "impl.py:Backend"
    """`<file>.py:<ClassName>` inside the backend folder; constructed as `Class(settings)`."""
    default: bool = False
    """Selected for its kind when settings.backends has no entry (at most one per kind)."""
    requires_env: list[str] = Field(default_factory=list)
    requires_binaries: list[str] = Field(default_factory=list)
    """Executables that must be on PATH; reported by `auoreo doctor`."""
    license: str = ""
    posture: Literal["ok", "gpl-isolated", "agpl-isolated", "nc-opt-in"] = "ok"
    """License posture surfaced by doctor (spec §13): GPL code runs in a subprocess only,
    AGPL code only in a separate sidecar process; non-commercial weights need an opt-in."""
    override: bool = False

    @field_validator("impl")
    @classmethod
    def _impl_ref(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*", v) or ".." in v:
            raise ValueError("impl must look like 'impl.py:Backend' inside the backend folder")
        return v

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.name}"


HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SubagentStop",
    "transform",
]


class HookManifest(_Strict):
    """`hooks/<name>.yaml`. A command hook gets the event as JSON on stdin and answers with
    `{"decision": "allow"|"deny"|"modify", "message"?: str, "input"?: dict}` (Stop hooks:
    `{"block": bool, "reason"?: str}`). Non-zero exit = error → `on_error` policy."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    event: HookEvent
    matcher: str = "*"
    """fnmatch over the tool name (tool events), command name, or agent name (session events)."""
    command: list[str] = Field(min_length=1)
    """argv, run with the scrubbed child env and cwd = session cwd; no shell."""
    timeout_ms: int = Field(default=5_000, ge=100, le=300_000)
    on_error: Literal["warn", "block"] = "warn"
    enabled: bool = True


class PolicyManifest(_Strict):
    """`policies/<name>.star` (M6): a Starlark exec policy. The header is `# key: value`
    comment lines before the first code line; the file must define `decide(req)`, which
    returns "allow" | "deny" | "ask" | "review" | None (no opinion). Evaluated after floors
    and before permission rules; higher `priority` runs first, first non-None answer wins."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class JobManifest(_Strict):
    """`jobs/<name>.yaml`. APScheduler only fires the trigger; AUOREO runs the job in its own
    session and records `job_runs` + `deliveries`."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    schedule: str = Field(min_length=1)
    """5-field cron (`0 9 * * 1-5`), `every 30m|2h|1d`, a bare duration (`30m` = every 30m),
    `every monday 9am [<tz>]`, or an ISO-8601 one-shot datetime."""
    tz: str | None = None
    """IANA zone for cron schedules; default = process zone."""
    agent: str = Field(default="default", pattern=NAME_RE)
    workflow: str | None = None
    prompt: str = Field(min_length=1)
    """Template; `{{now}}` and `{{job}}` are available."""
    skills: list[str] = Field(default_factory=list)
    deliver: list[str] = Field(default_factory=lambda: ["log"])
    """Delivery targets, tried in order (first success wins): `backends/delivery/<name>[:target]`
    or an alias — `origin` (where the job was created), `home` (`suggestions_home`), `all`
    (fan-out to every healthy provider)."""
    enabled: bool = True
    catch_up: bool = True
    """Fire once if a run was missed while the server was down (within half a period, max 2h)."""
    timeout_s: int | None = Field(default=None, ge=10, le=86_400)
    """Hard interrupt per run; default `teams2.job_timeout_s` (spec §16.7: 3 minutes)."""
    max_steps: int | None = Field(default=None, ge=1, le=500)
    # --- I3 (spec §16.7, §25.7, §25.11)
    model: str | None = Field(default=None, max_length=255)
    """Model alias/id for the run (`provider` prefixes it when it has no `/`)."""
    provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    script: str | None = Field(default=None, max_length=255)
    """Pre-run script under `<home>/scripts/`; its stdout (sandboxed read-only, redacted, capped,
    wrapped as UNTRUSTED data) is injected before the prompt."""
    context_from: list[str] = Field(default_factory=list)
    """`previous` (this job's last output) and/or other job names whose last output is injected."""
    workdir: str | None = Field(default=None, max_length=1024)
    """Working directory of the script (and the run's context note); must exist."""
    origin: str | None = Field(default=None, max_length=200)
    """Delivery spec `deliver: [origin]` resolves to (set by the cronjob tool from its session)."""
    origin_session: str | None = Field(default=None, max_length=64)
    notify_on_complete: bool = False
    """Inject a `<job-notification>` into `origin_session` when a run finishes (§25.7)."""
    header: str | None = Field(default=None, max_length=500)
    """Delivery header override (`{job}`, `{when}`, `{run_id}`); "" disables; None = settings."""
    footer: str | None = Field(default=None, max_length=500)


class PluginManifest(_Strict):
    """`plugins/<name>/plugin.yaml` (+ `pyproject.toml`). Plugins run out of process in their
    own venv and talk JSON-RPC over stdio; what they provide is registered dynamically."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+")
    description: str = Field(min_length=1)
    provides: list[Literal["tools", "delivery", "connector", "backend", "skills"]] = Field(min_length=1)
    consumes: list[str] = Field(default_factory=list)
    """Capabilities the plugin needs from the host (`sessions`, `memory`, `secrets:<name>`)."""
    permissions: list[str] = Field(default_factory=list)
    """Permission names the plugin's tools use (shown at install time)."""
    config_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    entry: str = "plugin.py:Plugin"
    requires_python: str = ">=3.12"
    sha256: str | None = None
    """Lock hash of the plugin folder; set by `auoreo plugin add`, verified by doctor."""
    enabled: bool = True

    @field_validator("config_schema")
    @classmethod
    def _object_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") != "object":
            raise ValueError("config_schema must be a JSON Schema with type: object")
        return v


class PermissionFile(_Strict):
    """`permissions/<name>.yaml`."""

    rules: list[Rule] = Field(default_factory=list)
    floors: list[str] = Field(default_factory=list)
    """Extra shell deny-globs evaluated before any mode (unbypassable)."""


class PromptSection(_Strict):
    """Frontmatter of `prompts/<name>.md`."""

    name: str = Field(pattern=NAME_RE)
    order: int
    tier: Literal["stable", "context"] = "stable"
    role: Literal["section", "compaction", "guardian"] = "section"
    """`section` joins the agent's system prompt; `compaction` is the summarizer instruction;
    `guardian` is the smart-mode permission policy. Only `section` reaches the agent."""
    complete: bool = False
    """A `complete: true` section is the whole system prompt: it shadows every other section
    and family overlay (at most one may exist across all roots)."""


class PromptFamily(_Strict):
    """Frontmatter of `prompts/families/<name>.md`; appended for matching models."""

    name: str = Field(pattern=NAME_RE)
    match: list[str] = Field(default_factory=list)
    """Regexes over the resolved model id. Empty = fallback family."""
    mode: str | None = None
    """A mode reminder (e.g. `plan`): never picked by model id; its text rides the per-turn
    context block while the session is in that permission mode (cache-safe)."""


class Variant(_Strict):
    """One `(provider, model_regex)` shaping entry from `prompts/variants.yaml`."""

    provider: str = "*"
    model_regex: str = ".*"
    omit_fields: list[str] = Field(default_factory=list)
    temperature: float | None = None
    reasoning_vocab: dict[str, str] | None = None
    """AUOREO effort (`low|medium|high|max`) → provider value. A level missing from the map
    clamps to the nearest weaker one listed. Unset → built-in default (`max→high` off
    Opus-class). A list form (`[low, high]`) is shorthand for an identity map."""
    reasoning: Literal["on", "off", "auto"] = "auto"
    """`off` omits every reasoning field; `on` always sends one (default `medium`);
    `auto` sends the resolved effort only when one is set."""
    thinking_display: Literal["hidden", "summary", "full"] | None = None
    block_binding: Literal["keep", "strip"] = "keep"
    """`strip` drops prior assistant `thinking_blocks`/`reasoning_content` before sending."""
    interleaved_field: str | None = None
    """Assistant-message field carrying interleaved reasoning (e.g. `reasoning_content`)."""
    rename_fields: dict[str, str] = Field(default_factory=dict)
    """Request field renames applied last (e.g. `max_tokens: max_completion_tokens`)."""

    @field_validator("reasoning_vocab", mode="before")
    @classmethod
    def _vocab_list(cls, v: Any) -> Any:
        return {str(x): str(x) for x in v} if isinstance(v, list) else v


UNLISTED_TOOLS = "<unlisted-tools>"


class VariantsFile(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    variants: list[Variant] = Field(default_factory=list)
    tool_order: list[str] = Field(default_factory=lambda: [UNLISTED_TOOLS], alias="toolOrder")
    """Tool order for the prompt's tool list and the request's tool schemas (spec §12). Must
    contain `<unlisted-tools>` exactly once: every tool not named here goes there, by name."""

    @field_validator("tool_order")
    @classmethod
    def _one_placeholder(cls, v: list[str]) -> list[str]:
        if v.count(UNLISTED_TOOLS) != 1:
            raise ValueError(f"toolOrder must contain '{UNLISTED_TOOLS}' exactly once")
        if len(set(v)) != len(v):
            raise ValueError("toolOrder lists a tool more than once")
        return v


class CommandManifest(_Strict):
    """Frontmatter of `commands/<name>.md`; the body is a `{{args}}`/`{{input}}` template.

    `prompt` commands expand into a chat message; `compact`, `model` and `plan` are
    handled by the engine (spec §2.11).
    """

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    handler: Literal["prompt", "compact", "model", "plan"] = "prompt"
    args_hint: str = ""
    # --- G6: `subtask: true` runs the rendered body as a background child session (`agent`),
    # optionally in an isolated worktree that carries the working changes (`worktree: true`).
    subtask: bool = False
    agent: str | None = Field(default=None, pattern=NAME_RE)
    worktree: bool = False
