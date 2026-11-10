"""Manifest contracts for every filesystem-defined capability.

Each kind of folder entry (agent, tool, skill, workflow, MCP server, permission file,
prompt section) is validated against one of these Pydantic models at discovery time.
Unknown keys are rejected (`extra="forbid"`) so typos fail loudly; skills follow the Agent
Skills format, which allows vendor keys, so unknown keys are ignored there.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from auoreo.harness.manifests_jobs import JobManifest as JobManifest  # re-export
from auoreo.harness.manifests_mcp import McpRoots as McpRoots  # re-export
from auoreo.harness.manifests_mcp import McpSampling as McpSampling  # re-export
from auoreo.harness.manifests_mcp import McpServerManifest as McpServerManifest  # re-export
from auoreo.harness.manifests_mcp import McpToolOverride as McpToolOverride  # re-export
from auoreo.harness.manifests_webhook import WebhookFilter as WebhookFilter  # re-export
from auoreo.harness.manifests_webhook import WebhookManifest as WebhookManifest  # re-export

NAME_RE = r"^[a-z][a-z0-9_]*$"
SLUG_RE = r"^[a-z0-9][a-z0-9._-]*$"
_DEVICES = (f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10))
RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", *_DEVICES})
"""Windows device names: never a definition file name, with or without an extension."""


def check_reserved(name: str) -> str:
    """Field validator body: refuse a Windows reserved device name (any letter case)."""
    if name.lower() in RESERVED_NAMES:
        raise ValueError(f"'{name}' is a reserved device name on Windows")
    return name


Action = Literal["allow", "ask", "deny"]
NET_JUSTIFICATION = {  # --- I9: injected into `net: direct` tools (checked by tools/netstage.py)
    "type": "string",
    "maxLength": 500,
    "description": "Why this call must bypass the egress proxy (net: direct; a human reviews it).",
}


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
    # --- I6: progress contract (spec §25.7 L977)
    progress_schema: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("progress_schema", "progressSchema")
    )
    """JSON Schema for the `data` of `ctx.progress(..., data=...)`; an invalid payload is dropped."""
    activity_hint: str = Field(
        default="", max_length=120, validation_alias=AliasChoices("activity_hint", "activityHint")
    )
    """Short UI label while running, e.g. `Reading {path}` (`{arg}` from the call's arguments)."""

    @field_validator("progress_schema")
    @classmethod
    def _progress_schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            from jsonschema import Draft202012Validator, SchemaError

            try:
                Draft202012Validator.check_schema(v)
            except SchemaError as exc:
                raise ValueError(f"progressSchema is not a valid JSON Schema: {exc.message}") from exc
        return v

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

    @model_validator(mode="after")
    def _net_direct(self) -> ToolManifest:
        if self.net == "direct":  # I9 (§25.10): direct egress is approval + justification
            props = self.parameters.setdefault("properties", {})
            props.setdefault("net_justification", dict(NET_JUSTIFICATION))
        return self

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
    memory_tenant: str | None = Field(default=None, pattern=SLUG_RE, max_length=48)
    """M7: the fact-store partition `agent:<slug>` this agent reads and writes (honoured when
    `m7.agents.memory_tenants` is on; MEMORY.md / USER.md / PREFS.md stay shared)."""


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


class PluginManifest(_Strict):
    """`plugins/<name>/plugin.yaml` (+ `pyproject.toml`). Plugins run out of process in their
    own venv and talk JSON-RPC over stdio; what they provide is registered dynamically."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+")
    description: str = Field(min_length=1)
    provides: list[Literal["tools", "delivery", "connector", "backend", "skills", "webhook_provider"]] = (
        Field(min_length=1)
    )
    consumes: list[str] = Field(default_factory=list)
    """Capabilities the plugin needs from the host (`sessions`, `memory`, `usage`, `secrets:<name>`,
    `oauth:<connector>` for an access token minted from `oauth/<connector>`)."""
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

    def is_module_entry(self, folder: Any) -> bool:
        """I6: a pip-installed plugin (`requirements.lock` in its folder) names an importable
        `package.module:Class` entry point instead of a `file.py:Class` inside the folder."""
        module = self.entry.split(":", 1)[0]
        is_dotted = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", module) is not None
        return is_dotted and not module.endswith(".py") and (folder / "requirements.lock").is_file()


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

    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    """--- I5: `auoreo run --variant <name>` picks this entry by name (exact match first)."""
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
    named: list[Variant] = Field(default_factory=list)
    """--- I5: request presets chosen only by `--variant <name>` (never matched by model id)."""
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

    @model_validator(mode="after")
    def _unique_names(self) -> VariantsFile:
        if any(v.name is None for v in self.named):
            raise ValueError("every `named:` variant needs a `name`")
        names = [v.name for v in [*self.variants, *self.named] if v.name]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        return self


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
