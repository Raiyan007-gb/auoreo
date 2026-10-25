"""F2 `gh_*` tools over a fake `gh` executable on PATH (spec §2.6)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolError

FAKE_GH = """#!{python}
import json, sys, pathlib
here = pathlib.Path(__file__).parent
with open(here / "gh.log", "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
mode = (here / "mode").read_text().strip() if (here / "mode").exists() else "ok"
if mode == "unauth":
    sys.stderr.write("To get started with GitHub CLI, please run:  gh auth login\\n")
    sys.exit(4)
if mode == "notfound":
    sys.stderr.write("GraphQL: Could not resolve to a PullRequest with the number of 99.\\n")
    sys.exit(1)
if mode == "big":
    print("x" * 100000)
    sys.exit(0)
if mode == "pending":
    print('[{{"name": "ci", "bucket": "pending"}}]')
    sys.exit(8)
print(json.dumps({{"argv": sys.argv[1:], "title": "Fake PR"}}))
"""

ALLOW_ALL = [
    Rule(permission="github_read", pattern="*", action="allow"),
    Rule(permission="github_write", pattern="*", action="allow"),
]


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    script = bindir / "gh"
    script.write_text(FAKE_GH.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


@pytest.fixture
def repo_dir(workdir: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    subprocess.run(
        ["git", "-C", str(workdir), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True
    )
    return workdir


def calls(bindir: Path) -> list[list[str]]:
    log = bindir / "gh.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


async def call(
    services: Services,
    cwd: Path,
    name: str,
    args: dict,
    *,
    rules: list[Rule] | None = None,
    interactive: bool = False,
) -> tuple[str, bool]:
    ctx = ToolContext(session_id="s1", cwd=cwd, home=services.settings.home, interactive=interactive)
    engine = PermissionEngine(ALLOW_ALL if rules is None else rules)
    ctx.services["engine"] = engine
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)), tools=services.harness.tools, ctx=ctx, engine=engine
    )
    return out.result.content, out.result.is_error


GH_TOOLS = [
    "gh_pr_view",
    "gh_pr_list",
    "gh_pr_diff",
    "gh_pr_checks",
    "gh_issue_view",
    "gh_issue_list",
    "gh_run_list",
    "gh_run_view",
    "gh_repo_view",
    "gh_api",
    "gh_api_write",
    "gh_pr_create",
    "gh_pr_comment",
    "gh_pr_merge",
    "gh_issue_create",
    "gh_issue_comment",
]
WRITE_TOOLS = {
    "gh_api_write",
    "gh_pr_create",
    "gh_pr_comment",
    "gh_pr_merge",
    "gh_issue_create",
    "gh_issue_comment",
}


def test_manifests(services: Services) -> None:
    for name in GH_TOOLS:
        m = services.harness.tools[name].manifest
        assert m.exposure == "deferred" and m.toolset == "github" and m.net == "direct", name
        assert "GH_TOKEN" in m.requires_env and "GITHUB_TOKEN" in m.requires_env
        if name in WRITE_TOOLS:
            assert (m.permission_name, m.default_action, m.is_destructive) == ("github_write", "ask", True)
        else:
            assert (m.permission_name, m.default_action, m.is_read_only) == ("github_read", "allow", True)


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://token@github.com/acme/widgets.git", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets.git", "acme/widgets"),
        ("https://ghe.corp.example/team/svc.git", "ghe.corp.example/team/svc"),
        ("/srv/git/local.git", None),
    ],
)
def test_parse_remote(url: str, slug: str | None) -> None:
    assert ghcli.parse_remote(url) == slug


def test_allow_list_and_endpoint_scoping() -> None:
    assert ghcli.allowed("ACME/Widgets", "acme/widgets", [])
    assert not ghcli.allowed("evil/repo", "acme/widgets", [])
    assert ghcli.allowed("acme/other", "acme/widgets", ["acme/*"])
    assert ghcli.api_endpoint("/repos/{owner}/{repo}/pulls?state=all", "acme/widgets") == (
        "repos/acme/widgets/pulls?state=all"
    )
    for bad in ("user", "graphql", "repos/evil/repo/issues", "repos/acme/widgets/../../x", "search/issues"):
        with pytest.raises(ToolError):
            ghcli.api_endpoint(bad, "acme/widgets")
    assert ghcli.field_args({"title": "@/etc/passwd", "n": 2, "draft": True}) == [
        "-f",
        "title=@/etc/passwd",  # raw string: never read as a file
        "-F",
        "n=2",
        "-F",
        "draft=true",
    ]
    assert ghcli.flag("title", "-x") == ["--title=-x"]


async def test_pr_view_argv_and_default_repo(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    text, err = await call(services, repo_dir, "gh_pr_view", {"number": 7, "comments": True})
    assert not err, text
    argv = calls(fake_gh)[-1]
    assert argv[:4] == ["pr", "view", "7", "--repo=acme/widgets"]
    assert argv[4].startswith("--json=number,title") and argv[4].endswith(",comments")
    assert "Fake PR" in text


async def test_read_tools_build_expected_argv(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    cases = [
        ("gh_pr_list", {"state": "all", "limit": 5, "search": "-x review:required"}, ["pr", "list"]),
        ("gh_pr_diff", {"number": 3, "name_only": True}, ["pr", "diff", "3"]),
        ("gh_issue_view", {"number": 9}, ["issue", "view", "9"]),
        ("gh_issue_list", {"label": "bug"}, ["issue", "list"]),
        ("gh_run_list", {"branch": "main"}, ["run", "list"]),
        ("gh_run_view", {"run_id": 42}, ["run", "view", "42"]),
        ("gh_repo_view", {}, ["repo", "view", "acme/widgets"]),
        ("gh_api", {"endpoint": "repos/{owner}/{repo}/pulls", "params": {"state": "open"}}, ["api"]),
    ]
    for name, args, prefix in cases:
        text, err = await call(services, repo_dir, name, args)
        assert not err, (name, text)
        argv = calls(fake_gh)[-1]
        assert argv[: len(prefix)] == prefix, (name, argv)
    log = calls(fake_gh)
    assert "--search=-x review:required" in log[0] and "--name-only" in log[1]
    assert log[-1][:4] == ["api", "--method", "GET", "repos/acme/widgets/pulls"]
    assert log[-1][4:6] == ["-f", "state=open"]


async def test_other_repo_refused_unless_allow_listed(
    services: Services, repo_dir: Path, fake_gh: Path, home: Path
) -> None:
    text, err = await call(services, repo_dir, "gh_issue_view", {"number": 1, "repo": "evil/repo"})
    assert err and "not allowed" in text and "github_allowed_repos" in text
    assert calls(fake_gh) == []
    svc = await Services.create(
        Settings(home=home, gateway="fake", github_allowed_repos=["evil/*"]), cwd=repo_dir, with_mcp=False
    )
    try:
        text, err = await call(svc, repo_dir, "gh_issue_view", {"number": 1, "repo": "evil/repo"})
        assert not err, text
        assert "--repo=evil/repo" in calls(fake_gh)[-1]
    finally:
        await svc.close()


async def test_no_remote_needs_repo(services: Services, workdir: Path, fake_gh: Path) -> None:
    text, err = await call(services, workdir, "gh_pr_list", {})
    assert err and "no GitHub remote" in text


async def test_write_tools_ask_by_default(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    args = {"title": "t", "body": "b"}
    text, err = await call(services, repo_dir, "gh_issue_create", args, rules=[])
    assert err and "needs approval" in text
    text, err = await call(services, repo_dir, "gh_pr_list", {}, rules=[])
    assert not err, text  # reads are allowed by default
    assert calls(fake_gh)[-1][:2] == ["pr", "list"]


async def test_write_argv_and_secret_refusal(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    text, err = await call(
        services, repo_dir, "gh_pr_create", {"title": "Fix", "body": "Body", "base": "main", "draft": True}
    )
    assert not err, text
    argv = calls(fake_gh)[-1]
    assert argv == [
        "pr",
        "create",
        "--repo=acme/widgets",
        "--title=Fix",
        "--body=Body",
        "--base=main",
        "--draft",
    ]
    n = len(calls(fake_gh))
    secret = "here is my token ghp_" + "a" * 36
    for name, args in [
        ("gh_pr_comment", {"number": 1, "body": secret}),
        ("gh_issue_create", {"title": "x", "body": "GITHUB_TOKEN=abcdef123456"}),
        (
            "gh_api_write",
            {"method": "POST", "endpoint": "repos/{owner}/{repo}/labels", "fields": {"n": secret}},
        ),
    ]:
        text, err = await call(services, repo_dir, name, args)
        assert err and "secret" in text, (name, text)
    assert len(calls(fake_gh)) == n  # nothing was sent


async def test_api_write_method_and_scope(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    args = {"method": "PATCH", "endpoint": "repos/acme/widgets/issues/3", "fields": {"state": "closed"}}
    text, err = await call(services, repo_dir, "gh_api_write", args)
    assert not err, text
    assert calls(fake_gh)[-1] == [
        "api",
        "--method",
        "PATCH",
        "repos/acme/widgets/issues/3",
        "-f",
        "state=closed",
    ]
    text, err = await call(
        services, repo_dir, "gh_api_write", {"method": "DELETE", "endpoint": "user/keys/1"}
    )
    assert err and "repository endpoints" in text


async def test_merge_always_asks_a_human(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    text, err = await call(services, repo_dir, "gh_pr_merge", {"number": 5})
    assert err and "non-interactive" in text  # interactive_only
    task = asyncio.create_task(call(services, repo_dir, "gh_pr_merge", {"number": 5}, interactive=True))
    for _ in range(100):
        pending = services.approvals.pending()
        if pending:
            break
        await asyncio.sleep(0.02)
    assert pending and "auto-merge" in pending[0].detail  # asked despite the allow rule
    await services.approvals.reply(pending[0].request_id, "once")
    text, err = await task
    assert not err, text
    assert calls(fake_gh)[-1] == ["pr", "merge", "5", "--repo=acme/widgets", "--squash", "--auto"]


async def test_checks_pending_is_not_an_error(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    (fake_gh / "mode").write_text("pending")
    text, err = await call(services, repo_dir, "gh_pr_checks", {"number": 2})
    assert not err and "pending" in text and "[gh exit code 8]" in text


async def test_errors_are_honest(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    (fake_gh / "mode").write_text("unauth")
    text, err = await call(services, repo_dir, "gh_pr_view", {"number": 1})
    assert err and "gh auth login" in text and "GITHUB_TOKEN" in text
    (fake_gh / "mode").write_text("notfound")
    text, err = await call(services, repo_dir, "gh_pr_view", {"number": 99})
    assert err and "not found" in text


async def test_output_is_capped(services: Services, repo_dir: Path, fake_gh: Path) -> None:
    (fake_gh / "mode").write_text("big")
    text, err = await call(services, repo_dir, "gh_issue_list", {})
    assert not err and len(text) < 25_000 and "chars omitted" in text


async def test_missing_gh(
    services: Services, repo_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    text, err = await call(services, repo_dir, "gh_pr_list", {})
    assert err and "not installed" in text and "cli.github.com" in text


async def test_token_from_secret_store_reaches_gh(
    services: Services, repo_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.core.secrets import child_env

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-value")
    monkeypatch.setenv("SOME_OTHER_SECRET", "nope")
    ctx = ToolContext(session_id="s1", cwd=repo_dir, home=services.settings.home)
    ctx.env = child_env(services.harness.tools["gh_pr_view"].manifest.requires_env, services.secrets)
    env = ghcli.gh_env(ctx)
    assert env["GH_TOKEN"] == "test-token-value" and env["GH_PROMPT_DISABLED"] == "1"
    assert "SOME_OTHER_SECRET" not in env
