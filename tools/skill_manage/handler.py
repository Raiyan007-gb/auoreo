"""Skills-writer tool: writes skills to the runtime overlay root (`<home>/skills/`), never
the repo, validates them with `auoreo.memory.skill_lint`, then reloads the harness so the
skill is listed from the next turn."""

import shutil
from pathlib import Path, PurePosixPath

from auoreo.harness import frontmatter
from auoreo.harness.registry import HarnessError
from auoreo.memory import skill_lint
from auoreo.tools.context import ToolContext, ToolError

MAX_FILES = 20


def _safe_rel(raw: str) -> PurePosixPath:
    rel = PurePosixPath(raw.replace("\\", "/"))
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or rel.parts[0].startswith("."):
        raise ToolError(f"file path '{raw}' must be relative, inside the skill folder")
    if rel.as_posix() == "SKILL.md":
        raise ToolError("use description/body for SKILL.md, not files")
    return rel


def _stage_files(files: dict) -> dict[PurePosixPath, str]:
    if len(files) > MAX_FILES:
        raise ToolError(f"at most {MAX_FILES} supporting files")
    staged = {_safe_rel(k): v for k, v in files.items()}
    for rel, text in staged.items():
        if skill_lint.looks_secret(text):
            raise ToolError(f"refusing to write {rel}: it looks like it contains a secret")
    return staged


async def _reload(ctx: ToolContext) -> str:
    svc = ctx.services.get("services")
    harness = svc.harness if svc is not None else ctx.harness
    if harness is None:
        return ""
    try:
        diff = harness.reload()
    except HarnessError as exc:
        raise ToolError("harness reload failed: " + "; ".join(str(e) for e in exc.errors)) from exc
    if svc is not None:
        if svc.mcp is not None:
            await svc.mcp.sync()
        gen, errors = diff.pop("generation"), diff.pop("errors")
        await svc.emit("server", "harness.reloaded", {"generation": gen, "errors": errors, **diff})
    return " (harness reloaded)"


def _existing(ctx: ToolContext, name: str):
    return ctx.harness.skills.get(name) if ctx.harness is not None else None


def _lint(args: dict, ctx: ToolContext, name: str) -> str:
    if "description" in args or "body" in args:
        errors = skill_lint.lint(name, args.get("description", ""), args.get("body", ""))
        label = name
    else:
        skill = _existing(ctx, name)
        if skill is None:
            raise ToolError(f"unknown skill '{name}'")
        errors = skill_lint.lint_file(skill.path)
        label = f"{name} ({skill.source}: {skill.path})"
    if errors:
        raise ToolError(f"{label}: " + "; ".join(errors))
    return f"{label}: ok"


async def run(args: dict, ctx: ToolContext) -> str:
    action, name = args["action"], args["name"]
    if action == "lint":
        return _lint(args, ctx, name)
    root = ctx.home / "skills"
    folder = root / name
    if action == "delete":
        if not (folder / "SKILL.md").is_file():
            raise ToolError(f"no skill '{name}' in {root} (only user skills can be deleted)")
        shutil.rmtree(folder)
        return f"deleted skill '{name}'" + await _reload(ctx)
    if action == "create":
        existing = _existing(ctx, name)
        if existing is not None or folder.exists():
            where = f"{existing.source}: {existing.path}" if existing is not None else str(folder)
            raise ToolError(f"skill '{name}' already exists ({where}); pick another name or use update")
        description, body = args.get("description", ""), args.get("body", "")
    else:  # update
        if not (folder / "SKILL.md").is_file():
            raise ToolError(f"no skill '{name}' in {root}; use create")
        old, old_body, _ = frontmatter.split((folder / "SKILL.md").read_text(encoding="utf-8"))
        description = args.get("description", str(old.get("description", "")))
        body = args.get("body", old_body)
    errors = skill_lint.lint(name, description, body, folder.name)
    if errors:
        raise ToolError("skill rejected: " + "; ".join(errors))
    staged = _stage_files(args.get("files") or {})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(skill_lint.render(name, description, body), encoding="utf-8")
    for rel, text in staged.items():
        dest = folder / Path(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    verb = "created" if action == "create" else "updated"
    extra = f" with {len(staged)} supporting file(s)" if staged else ""
    return f"{verb} skill '{name}' at {folder / 'SKILL.md'}{extra}" + await _reload(ctx)
