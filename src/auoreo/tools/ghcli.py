"""Engine side of the `gh_*` tools (spec §2.6): argv building, repo resolution and the
allow-list, secret refusal, error mapping. Handlers in `tools/gh_*/` stay thin.

Every call shells out to the GitHub CLI (`gh`) as an argv list (never a shell string)
through `ctx.backend("sandbox")`. Auth is whatever `gh` already has (`gh auth login`) or
`GH_TOKEN` / `GITHUB_TOKEN` from env → keyring → `secrets.db` (declared in the tool's
`requires_env`, so the executor copies only those names into the scrubbed env).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from auoreo.core.redaction import redact
from auoreo.tools.context import ToolContext, ToolError, ToolResult

CAP = 20_000
"""Default output cap (chars) for one gh call; the executor's pruner still applies after."""

REPO_RE = re.compile(
    r"^(?:(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)$"
)
_REMOTE_RES = (
    re.compile(r"^[\w.-]+@(?P<host>[^:/]+):(?P<path>[^\s]+?)(?:\.git)?/?$"),  # git@host:o/r.git
    re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>[^\s]+?)(?:\.git)?/?$"),
)
_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9_.\-/{}?=&,%+:@~]+$")

INSTALL_HINT = (
    "the GitHub CLI `gh` is not installed or not on PATH. Install it from https://cli.github.com/ "
    "then authenticate with `gh auth login`, or store a token with `auoreo secrets set GITHUB_TOKEN`"
)
AUTH_HINT = (
    "gh is not authenticated for this host. Run `gh auth login`, or store a token with "
    "`auoreo secrets set GITHUB_TOKEN` (fine-grained, scoped to the repos you allow)"
)
# (substring in lowercase stderr, model-facing message). First match wins.
_ERRORS: tuple[tuple[str, str], ...] = (
    ("gh auth login", AUTH_HINT),
    ("http 401", AUTH_HINT),
    ("bad credentials", AUTH_HINT),
    ("rate limit", "GitHub API rate limit hit; wait and retry later, or authenticate to raise the limit"),
    ("could not resolve to a", "not found on GitHub (wrong number, or no access to the repository)"),
    ("http 404", "not found on GitHub (wrong endpoint or number, or no access to the repository)"),
    ("http 403", "GitHub refused the request (HTTP 403): the token lacks a scope or the repo blocks it"),
    ("http 422", "GitHub rejected the request as invalid (HTTP 422)"),
)
_AUTH_EXIT = 4  # gh: "authentication required"


def clip(text: str, cap: int = CAP) -> str:
    """Head + tail clip with an omission marker (like `bash`)."""
    if len(text) <= cap:
        return text
    head, tail = text[: cap * 3 // 4], text[-cap // 5 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


def tail_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return f"… [{len(lines) - n} earlier lines omitted] …\n" + "\n".join(lines[-n:])


def flag(name: str, value: Any) -> list[str]:
    """`--name=value` (one argv item, so a value starting with `-` is never a flag);
    `True` → `--name`; `None`/`False`/`""` → nothing."""
    if value is None or value is False or value == "":
        return []
    if value is True:
        return [f"--{name}"]
    return [f"--{name}={value}"]


def refuse_secrets(**fields: Any) -> None:
    """Refuse to send text that the redactor would mask (tokens, keys, `X_TOKEN=…`)."""
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and v and redact(v) != v:
                raise ToolError(
                    f"refusing to send `{key}`: it looks like it contains a secret (token, key or "
                    "credential assignment). Remove it and retry."
                )


def parse_remote(url: str) -> str | None:
    """`[host/]owner/name` from a git remote URL; github.com is left implicit."""
    for rx in _REMOTE_RES:
        m = rx.match(url.strip())
        if m:
            parts = m.group("path").strip("/").split("/")
            if len(parts) != 2:
                return None
            host = m.group("host").lower()
            slug = "/".join(parts)
            return slug if host in {"github.com", "www.github.com"} else f"{host}/{slug}"
    return None


async def _git(cwd: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG"}}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = await asyncio.create_subprocess_exec(
            git,
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, TimeoutError):
        return None
    return out.decode(errors="replace").strip() if proc.returncode == 0 else None


async def cwd_repo(cwd: Path) -> str | None:
    """The cwd checkout's GitHub repo from `origin` (else the first remote), or None."""
    url = await _git(cwd, "remote", "get-url", "origin")
    if url is None:
        names = (await _git(cwd, "remote")) or ""
        first = names.split()[0] if names.split() else None
        url = await _git(cwd, "remote", "get-url", first) if first else None
    return parse_remote(url) if url else None


def _norm(repo: str) -> str:
    return repo.lower().removeprefix("github.com/")


def allowed(repo: str, home_repo: str | None, globs: list[str]) -> bool:
    """The cwd repo is always allowed; others must match a `github_allowed_repos` glob."""
    r = _norm(repo)
    if home_repo is not None and r == _norm(home_repo):
        return True
    return any(fnmatch.fnmatchcase(r, _norm(g)) for g in globs)


def _settings_globs(ctx: ToolContext) -> list[str]:
    svc = ctx.services.get("services")
    return list(getattr(getattr(svc, "settings", None), "github_allowed_repos", []) or [])


async def repo_for(ctx: ToolContext, raw: str | None) -> str:
    """Resolve and allow-list the target repo (`[HOST/]OWNER/REPO`)."""
    find_gh(ctx)  # say "install gh" before anything about remotes
    home_repo = await cwd_repo(ctx.cwd)
    if not raw:
        if home_repo is None:
            raise ToolError(
                "no repository: the working directory has no GitHub remote. Pass repo='owner/name' "
                "(and list it in settings.github_allowed_repos)"
            )
        return home_repo
    repo = raw.strip().removeprefix("https://").removeprefix("github.com/").removesuffix(".git")
    if not REPO_RE.match(repo):
        raise ToolError(f"repo must look like owner/name (or host/owner/name), got `{raw[:100]}`")
    if not allowed(repo, home_repo, _settings_globs(ctx)):
        where = f"the working directory's repo `{home_repo}`" if home_repo else "no cwd repo"
        raise ToolError(
            f"repo `{repo}` is not allowed: only {where} and repos matching settings.github_allowed_repos "
            "may be used. Ask the user to add it to that list"
        )
    return repo


def api_endpoint(endpoint: str, repo: str) -> str:
    """Validate a `gh api` endpoint: repository-scoped only (`repos/OWNER/REPO[/...]`), with
    `{owner}`/`{repo}` placeholders filled from the resolved repo. The repo segment must be
    `repo` (already allow-listed by `repo_for`)."""
    ep = endpoint.strip()
    for prefix in ("https://api.github.com/", "http://api.github.com/", "/"):
        ep = ep.removeprefix(prefix)
    slug = repo.split("/", 1)[1] if repo.count("/") == 2 else repo
    owner, name = slug.split("/")
    ep = ep.replace("{owner}", owner).replace("{repo}", name)
    if "://" in ep or ".." in ep or not _ENDPOINT_RE.match(ep):
        raise ToolError(
            f"invalid endpoint `{endpoint[:120]}`: use a path like repos/{{owner}}/{{repo}}/pulls"
        )
    parts = ep.split("?", 1)[0].split("/")
    if len(parts) < 3 or parts[0] != "repos":
        raise ToolError(
            "only repository endpoints are reachable: repos/{owner}/{repo}/... "
            "(no user, org, search or graphql)"
        )
    if f"{parts[1]}/{parts[2]}".lower() != slug.lower():
        raise ToolError(
            f"endpoint repo `{parts[1]}/{parts[2]}` differs from the allowed repo `{slug}`; pass repo="
        )
    return ep


def field_args(fields: dict[str, Any] | None) -> list[str]:
    """`-f key=value` for strings (never `@file`), `-F` only for bool/int/None literals."""
    out: list[str] = []
    for key, value in (fields or {}).items():
        if not re.match(r"^[A-Za-z0-9_\[\]]+$", key):
            raise ToolError(f"invalid field name `{key[:60]}`")
        if isinstance(value, bool) or value is None:
            out += ["-F", f"{key}={json.dumps(value)}"]
        elif isinstance(value, int | float):
            out += ["-F", f"{key}={value}"]
        else:
            out += ["-f", f"{key}={value}"]
    return out


def host_of(repo: str) -> str | None:
    return repo.split("/", 1)[0] if repo.count("/") == 2 else None


def find_gh(ctx: ToolContext) -> str:
    gh = shutil.which("gh", path=ctx.env.get("PATH", ""))
    if gh is None:
        raise ToolError(INSTALL_HINT)
    return gh


def gh_env(ctx: ToolContext) -> dict[str, str]:
    env = dict(ctx.env)
    if "GH_TOKEN" not in env and env.get("GITHUB_TOKEN"):
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
    env.update({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1", "GH_PAGER": "cat"})
    return env


def map_error(code: int, stderr: str) -> str:
    low = stderr.lower()
    for needle, message in _ERRORS:
        if needle in low:
            return f"{message}\n[gh exit code {code}] {clip(stderr.strip(), 1500)}"
    if code == _AUTH_EXIT:
        return f"{AUTH_HINT}\n[gh exit code {code}]"
    return f"gh failed [exit code {code}]: {clip(stderr.strip(), 3000) or '(no output)'}"


async def run_gh(
    ctx: ToolContext,
    argv: list[str],
    *,
    repo: str | None = None,
    cap: int = CAP,
    ok_codes: tuple[int, ...] = (0,),
    timeout_s: float = 60,
) -> ToolResult:
    """Run `gh <argv>` through the sandbox backend; map failures to honest messages."""
    from auoreo.backends.sandbox import ExecSpec, SandboxError

    env = gh_env(ctx)
    gh = find_gh(ctx)
    policy = ctx.exec_policy
    spec = ExecSpec(
        argv=[gh, *argv],
        cwd=ctx.cwd,
        env=env,
        timeout_s=timeout_s,
        mode=policy.mode,  # type: ignore[arg-type]
        net=policy.net,  # type: ignore[arg-type]
    )
    try:
        res = await ctx.backend("sandbox").run(spec)
    except SandboxError as exc:
        raise ToolError(f"[sandbox: {exc}]") from exc
    if res.timed_out:
        raise ToolError(f"gh timed out after {timeout_s:.0f}s")
    meta = {"exit_code": res.exit_code, "repo": repo, "sandbox": res.backend}
    if res.exit_code not in ok_codes:
        return ToolResult(map_error(res.exit_code, res.stderr or res.stdout), is_error=True, meta=meta)
    text = res.stdout.strip() or "(no output)"
    note = f"\n[gh exit code {res.exit_code}]" if res.exit_code else ""
    return ToolResult(clip(text, cap) + note, meta=meta)


async def human_confirm(ctx: ToolContext, tool: str, permission: str, pattern: str, detail: str) -> None:
    """Explicit human approval that no rule or mode can pre-grant (used by `gh_pr_merge`).
    Skipped when the executor already asked for this call (no matching allow rule)."""
    engine = ctx.services.get("engine")
    if (
        engine is not None
        and engine.evaluate(permission, pattern, "ask", interactive=ctx.interactive).action == "ask"
    ):
        return  # the executor's own approval prompt covered this call
    svc = ctx.services.get("services")
    if not ctx.interactive or svc is None:
        raise ToolError(f"{tool} always needs a human approval and this run is non-interactive")
    outcome = await svc.approvals.request(ctx.session_id, tool, permission, pattern, detail[:2000])
    if not outcome.granted:
        raise ToolError(f"user rejected {tool} `{pattern}`")
