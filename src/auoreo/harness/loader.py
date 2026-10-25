"""Parse + validate candidates into typed definitions. Handler code is imported lazily."""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from auoreo.harness import frontmatter
from auoreo.harness.discovery import Candidate, Root
from auoreo.harness.manifests import (
    AgentManifest,
    BackendManifest,
    CommandManifest,
    HookManifest,
    JobManifest,
    McpServerManifest,
    PermissionFile,
    PluginManifest,
    PolicyManifest,
    PromptFamily,
    PromptSection,
    SkillManifest,
    ToolManifest,
    VariantsFile,
    WebhookManifest,
    WorkflowManifest,
)
from auoreo.tools.context import ToolContext, ToolResult

Handler = Callable[[dict[str, Any], ToolContext], Awaitable["str | ToolResult"]]
M = TypeVar("M", bound=BaseModel)

VAR_RE = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")
VAR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


class LoadError(Exception):
    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.line = line
        self.message = message
        loc = f"{path}:{line}" if line else str(path)
        super().__init__(f"{loc}: {message}")


@dataclass
class ToolDef:
    manifest: ToolManifest
    source: str
    path: Path | None  # tool folder; None for dynamic (MCP) tools
    _loader: Callable[[], Handler] | None = None
    _handler: Handler | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.manifest.name

    def handler(self) -> Handler:
        """Import (once) and return the handler callable."""
        if self._handler is None:
            if self._loader is None:
                raise LoadError(self.path or Path("?"), "tool has no handler loader")
            self._handler = self._loader()
        return self._handler


@dataclass
class BackendDef:
    """A discovered backend; its class is imported on first `load()`."""

    manifest: BackendManifest
    source: str
    path: Path  # backend folder
    _cls: type[Any] | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.manifest.key

    def load(self) -> type[Any]:
        if self._cls is None:
            file_part, cls_name = self.manifest.impl.split(":")
            file_path = (self.path / file_part).resolve()
            if self.path.resolve() not in file_path.parents or not file_path.is_file():
                raise LoadError(file_path, "backend impl must be a file inside its folder")
            mod_name = f"auoreo_backend_{self.manifest.kind}_{self.manifest.name}"
            spec = importlib.util.spec_from_file_location(mod_name, file_path)
            if spec is None or spec.loader is None:
                raise LoadError(file_path, "cannot import backend")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                sys.modules.pop(mod_name, None)
                raise LoadError(file_path, f"backend import failed: {exc!r}") from exc
            cls = getattr(module, cls_name, None)
            if not isinstance(cls, type):
                raise LoadError(file_path, f"'{cls_name}' must be a class")
            self._cls = cls
        return self._cls


@dataclass(frozen=True)
class AgentDef:
    manifest: AgentManifest
    body: str
    source: str
    path: Path

    @property
    def name(self) -> str:
        return self.manifest.name


@dataclass(frozen=True)
class SkillDef:
    manifest: SkillManifest
    source: str
    path: Path

    @property
    def name(self) -> str:
        return self.manifest.name

    def body(self) -> str:
        """Lazy: the full SKILL.md body is read only when the skill is invoked."""
        _, body, _ = frontmatter.split(self.path.read_text(encoding="utf-8"))
        return body


@dataclass(frozen=True)
class PromptDef:
    section: PromptSection
    template: str
    source: str
    path: Path


@dataclass(frozen=True)
class FamilyDef:
    family: PromptFamily
    text: str
    source: str
    path: Path


@dataclass(frozen=True)
class CommandDef:
    manifest: CommandManifest
    template: str
    source: str
    path: Path

    @property
    def name(self) -> str:
        return self.manifest.name


@dataclass(frozen=True)
class Loaded:
    """Generic holder for YAML-only kinds."""

    manifest: Any
    source: str
    path: Path


# --------------------------------------------------------------------------- helpers


def _validate[M: BaseModel](model: type[M], data: dict[str, Any], path: Path, text: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        pointer = "/" + "/".join(str(p) for p in err["loc"]) if err["loc"] else "/"
        first_key = str(err["loc"][0]) if err["loc"] else ""
        line = frontmatter.locate_key(text, first_key) if first_key else None
        more = f" (+{len(exc.errors()) - 1} more)" if len(exc.errors()) > 1 else ""
        raise LoadError(path, f"{pointer}: {err['msg']}{more}", line) from exc


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LoadError(path, f"unreadable: {exc}") from exc


def _parse_md(c: Candidate) -> tuple[dict[str, Any], str, str]:
    text = _read(c.path)
    try:
        data, body, _ = frontmatter.split(text)
    except frontmatter.FrontmatterError as exc:
        raise LoadError(c.path, str(exc), exc.line) from exc
    return data, body, text


def _parse_yaml(c: Candidate) -> tuple[dict[str, Any], str]:
    text = _read(c.path)
    try:
        return frontmatter.load_yaml(text), text
    except frontmatter.FrontmatterError as exc:
        raise LoadError(c.path, str(exc), exc.line) from exc


def _check_name(c: Candidate, declared: str, text: str) -> None:
    if declared != c.name:
        raise LoadError(
            c.path,
            f"name '{declared}' must match its folder/file name '{c.name}'",
            frontmatter.locate_key(text, "name"),
        )


def template_vars(template: str) -> list[str]:
    return [m.group(1) for m in VAR_RE.finditer(template)]


def check_template(template: str, path: Path, allowed: set[str] | None = None) -> None:
    """Strict `{{var}}`: malformed or unknown variables are load errors (spec §12)."""
    for var in template_vars(template):
        if not VAR_NAME_RE.match(var):
            raise LoadError(path, f"malformed template variable '{{{{{var}}}}}'")
        root = var.split(".")[0]
        if allowed is not None and root not in allowed:
            raise LoadError(path, f"unknown template variable '{{{{{var}}}}}'")


def _handler_loader(tool_dir: Path, ref: str, name: str) -> Callable[[], Handler]:
    file_part, func_name = ref.split(":")
    file_path = (tool_dir / file_part).resolve()

    def load() -> Handler:
        if tool_dir.resolve() not in file_path.parents:
            raise LoadError(file_path, "handler must live inside its tool folder")
        if not file_path.is_file():
            raise LoadError(file_path, "handler file not found")
        mod_name = f"auoreo_tool_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        if spec is None or spec.loader is None:
            raise LoadError(file_path, "cannot import handler")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(mod_name, None)
            raise LoadError(file_path, f"handler import failed: {exc!r}") from exc
        fn = getattr(module, func_name, None)
        if fn is None or not inspect.iscoroutinefunction(fn):
            raise LoadError(file_path, f"'{func_name}' must be an async function")
        return fn  # type: ignore[no-any-return]

    return load


# --------------------------------------------------------------------------- per kind


def load_tool(c: Candidate) -> ToolDef:
    data, text = _parse_yaml(c)
    manifest = _validate(ToolManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    tool_dir = c.path.parent
    handler_file = tool_dir / manifest.handler.split(":")[0]
    if not handler_file.is_file():
        raise LoadError(
            c.path,
            f"handler file '{handler_file.name}' not found",
            frontmatter.locate_key(text, "handler"),
        )
    return ToolDef(
        manifest,
        c.root.source,
        tool_dir,
        _handler_loader(tool_dir, manifest.handler, manifest.name),
    )


def load_agent(c: Candidate) -> AgentDef:
    data, body, text = _parse_md(c)
    manifest = _validate(AgentManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    check_template(body, c.path)
    return AgentDef(manifest, body.strip(), c.root.source, c.path)


def load_skill(c: Candidate) -> SkillDef:
    data, _body, text = _parse_md(c)
    manifest = _validate(SkillManifest, data, c.path, text)
    return SkillDef(manifest, c.root.source, c.path)


def load_workflow(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(WorkflowManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    for step in manifest.steps:
        check_template(step.input, c.path, allowed={"input", "steps"})
    return Loaded(manifest, c.root.source, c.path)


def load_mcp(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(McpServerManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    if manifest.transport == "stdio" and not manifest.command:
        raise LoadError(c.path, "stdio transport requires 'command'")
    if manifest.transport == "streamable-http" and not manifest.url:
        raise LoadError(c.path, "streamable-http transport requires 'url'")
    return Loaded(manifest, c.root.source, c.path)


def load_permissions(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    return Loaded(_validate(PermissionFile, data, c.path, text), c.root.source, c.path)


def load_variants(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    return Loaded(_validate(VariantsFile, data, c.path, text), c.root.source, c.path)


PROMPT_VARS = {
    "agent_name",
    "agent_description",
    "agent_instructions",
    "tools_list",
    "skills_index",
    "cwd",
    "model",
    "date",
    "permission_mode",
    "memory",
    "os",
    "locale",
    "tone",
}


def load_prompt(c: Candidate) -> PromptDef:
    data, body, text = _parse_md(c)
    section = _validate(PromptSection, data, c.path, text)
    _check_name(c, section.name, text)
    check_template(body, c.path, allowed=PROMPT_VARS)
    return PromptDef(section, body.strip(), c.root.source, c.path)


def load_family(c: Candidate) -> FamilyDef:
    data, body, text = _parse_md(c)
    fam = _validate(PromptFamily, data, c.path, text)
    _check_name(c, fam.name, text)
    for rx in fam.match:
        try:
            re.compile(rx)
        except re.error as exc:
            raise LoadError(c.path, f"bad regex '{rx}': {exc}") from exc
    return FamilyDef(fam, body.strip(), c.root.source, c.path)


COMMAND_VARS = {"args", "input"}


def load_command(c: Candidate) -> CommandDef:
    data, body, text = _parse_md(c)
    manifest = _validate(CommandManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    check_template(body, c.path, allowed=COMMAND_VARS)
    return CommandDef(manifest, body.strip(), c.root.source, c.path)


def load_backend(c: Candidate) -> BackendDef:
    data, text = _parse_yaml(c)
    manifest = _validate(BackendManifest, data, c.path, text)
    folder = c.path.parent
    if manifest.kind != folder.parent.name or manifest.name != folder.name:
        raise LoadError(
            c.path,
            f"kind/name '{manifest.key}' must match the folders '{folder.parent.name}/{folder.name}'",
            frontmatter.locate_key(text, "name"),
        )
    impl_file = folder / manifest.impl.split(":")[0]
    if not impl_file.is_file():
        raise LoadError(
            c.path, f"impl file '{impl_file.name}' not found", frontmatter.locate_key(text, "impl")
        )
    return BackendDef(manifest, c.root.source, folder)


def load_hook(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(HookManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    return Loaded(manifest, c.root.source, c.path)


def load_job(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(JobManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    check_template(manifest.prompt, c.path, allowed={"now", "job"})
    return Loaded(manifest, c.root.source, c.path)


def load_webhook(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(WebhookManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    if not manifest.secret.startswith("secret:"):
        raise LoadError(
            c.path,
            "secret must be a 'secret:<name>' reference, never a value",
            frontmatter.locate_key(text, "secret"),
        )
    check_template(manifest.prompt, c.path, allowed={"event", "route", "delivery_id"})
    for f in manifest.filters:
        if f.regex is not None:
            try:
                re.compile(f.regex)
            except re.error as exc:
                raise LoadError(c.path, f"bad filter regex '{f.regex}': {exc}") from exc
    return Loaded(manifest, c.root.source, c.path)


def load_plugin(c: Candidate) -> Loaded:
    data, text = _parse_yaml(c)
    manifest = _validate(PluginManifest, data, c.path, text)
    _check_name(c, manifest.name, text)
    folder = c.path.parent
    entry_file = folder / manifest.entry.split(":")[0]
    if ".." in manifest.entry or not entry_file.is_file():
        raise LoadError(
            c.path,
            f"entry file '{manifest.entry.split(':')[0]}' not found in plugin folder",
            frontmatter.locate_key(text, "entry"),
        )
    if not (folder / "pyproject.toml").is_file():
        raise LoadError(c.path, "plugin folder needs a pyproject.toml (installed into its own venv)")
    return Loaded(manifest, c.root.source, c.path)


def load_policy(c: Candidate) -> Loaded:
    text = _read(c.path)
    header: dict[str, Any] = {"name": c.name}
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        key, sep, value = line.lstrip("# ").partition(":")
        if sep and key.strip() in {"description", "priority", "enabled"}:
            header[key.strip()] = value.strip()
    manifest = _validate(PolicyManifest, header, c.path, text)
    from auoreo.core.policy import check_policy  # Starlark compile + `decide(req)` check

    check_policy(c.path, c.name, text)
    return Loaded(manifest, c.root.source, c.path)


LOADERS: dict[str, Callable[[Candidate], Any]] = {
    "hooks": load_hook,
    "jobs": load_job,
    "webhooks": load_webhook,
    "plugins": load_plugin,
    "policies": load_policy,
    "backends": load_backend,
    "tools": load_tool,
    "agents": load_agent,
    "skills": load_skill,
    "workflows": load_workflow,
    "mcp": load_mcp,
    "permissions": load_permissions,
    "variants": load_variants,
    "prompts": load_prompt,
    "families": load_family,
    "commands": load_command,
}

__all__ = [
    "LOADERS",
    "BackendDef",
    "AgentDef",
    "CommandDef",
    "FamilyDef",
    "Handler",
    "LoadError",
    "Loaded",
    "PromptDef",
    "Root",
    "SkillDef",
    "ToolDef",
]
