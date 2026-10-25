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


class WorkflowStep(_Strict):
    id: str = Field(pattern=NAME_RE)
    agent: str = Field(pattern=NAME_RE)
    input: str = "{{input}}"
    """Template; `{{input}}` and `{{steps.<id>.output}}` are available."""


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
    role: Literal["section", "compaction"] = "section"
    """`section` joins the agent's system prompt; `compaction` is the summarizer instruction."""


class PromptFamily(_Strict):
    """Frontmatter of `prompts/families/<name>.md`; appended for matching models."""

    name: str = Field(pattern=NAME_RE)
    match: list[str] = Field(default_factory=list)
    """Regexes over the resolved model id. Empty = fallback family."""


class Variant(_Strict):
    """One `(provider, model_regex)` shaping entry from `prompts/variants.yaml`."""

    provider: str = "*"
    model_regex: str = ".*"
    omit_fields: list[str] = Field(default_factory=list)
    temperature: float | None = None
    reasoning_vocab: list[str] | None = None


class VariantsFile(_Strict):
    variants: list[Variant] = Field(default_factory=list)


class CommandManifest(_Strict):
    """Frontmatter of `commands/<name>.md`; the body is a `{{args}}`/`{{input}}` template.

    `prompt` commands expand into a chat message; `compact`, `model` and `plan` are
    handled by the engine (spec §2.11).
    """

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    handler: Literal["prompt", "compact", "model", "plan"] = "prompt"
    args_hint: str = ""
