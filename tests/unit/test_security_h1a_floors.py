"""H1a: argv-based hardline floors, the shell secret scanner, ask floors for recoverable git,
the oversized-payload guard and the shell gate for non-bash shell tools (spec §19.4, §19.7,
§24.9, §25.10)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core import floors
from auoreo.core import permissions as perm
from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

ALLOW_ALL = [Rule(permission="*", pattern="*", action="allow")]

HARDLINE = [
    "rm -rf /",
    "rm -rf --no-preserve-root /",
    "rm --recursive --force /",
    "rm -r --force /usr",
    "rm -rf /etc/",
    "rm -rf /etc",
    "rm -fr /home",
    "rm -rf /bin /boot",
    "rm -rf /var/",
    "rm -rf /lib64",
    "rm -Rf /opt/*",
    "rm -rf /root /sys /proc /dev",
    "rm -rf ~/",
    "rm -rf $HOME",
    "sudo rm -rf /var",
    "sudo -u root -- rm -rf /usr",
    "env FOO=1 sudo -n rm -r /lib",
    "nice -n 5 timeout 10 rm -rf /usr",
    "cd /tmp && rm -rf /boot",
    "true; rm -rf /etc",
    "ls | xargs rm -rf /",
    "bash -c 'rm -rf /opt'",
    'sh -lc "echo x && rm -rf /root"',
    'echo "$(rm -rf /sys)"',
    "echo `rm -rf /usr`",
    "eval rm -rf /",
    "/bin/rm -rf /",
    "r'm' -rf /",
    "find / -delete",
    "find /etc -exec rm -f {} +",
    "find ~ -name x -delete",
    "git push origin main --force",
    "git push -f",
    "git push -uf origin feat",
    "git -C repo push origin +main",
    "cd repo && git push --force origin HEAD",
    "mkfs.ext4 /dev/sda1",
    "mkfs -t xfs /dev/nvme0n1",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "echo x > /dev/sda",
    ":(){ :|:& };:",
    "bomb(){ bomb|bomb& };bomb",
    "kill -9 -1",
    "shutdown -h now",
    "systemctl reboot",
    "echo pw | sudo -S apt install x",
    "sudo --askpass ls",
    "docker run --privileged alpine",
    "chmod -R 777 /",
]

SECRET = [
    "cat .env",
    "cat ./.env",
    "less config/.env.local",
    "cat .env.production",
    "cat prod.env",
    "source .env",
    ". ./.env",
    "set -a; . .env; set +a",
    "cp .env /tmp/x",
    "grep KEY .env",
    "cat .env*",
    "head -c 100 ~/.auoreo/secrets.db",
    "sqlite3 secrets.db .dump",
    "cat $AUOREO_HOME/secrets.json",
    "cat /proc/self/environ",
    "tr '\\0' '\\n' < /proc/1/environ",
    "docker run --env-file .env app",
    "echo KEY=1 > .env",
    "git show HEAD:.env",
    "printenv OPENAI_API_KEY",
    "env | grep -i token",
    "printenv | grep SECRET",
    "keyring get auoreo secrets-key",
    "secret-tool lookup service auoreo",
    "security find-generic-password -s auoreo -w",
    "ps auxe",
    "bash -c 'cat .env'",
]

ALLOWED = [
    "rm -rf build/",
    "rm -rf ./node_modules /tmp/x",
    "rm -f /tmp/file",
    "find . -name '*.pyc' -delete",
    "git push origin main",
    "git push -u origin feat",
    "git status && git diff",
    "dd if=/dev/zero of=/dev/null count=1",
    "kill -1 1234",
    "cat .env.example",
    "cat .envrc",
    "echo .env >> .gitignore",
    "grep -n .env .gitignore",
    'git commit -m "ignore .env files"',
    "env",
    "printenv HOME",
    "env | wc -l",
    "ls *",
    "ps aux",
    "python -c 'print(1)' 2>/dev/null",
    'curl -H "Authorization: Bearer $API_TOKEN" https://example.com',
]

ASK = [
    "git reset --hard HEAD~1",
    "git checkout .",
    "git checkout -- .",
    "git restore .",
    "git clean -fd",
    "git branch -D feat",
    "git commit --amend --no-edit",
    "git commit --no-verify -m x",
    "git push --no-verify",
    "git push --force-with-lease",
    "git push origin --delete old",
    "git config user.email x@example.com",
    "make && git reset --hard",
]


@pytest.mark.parametrize("cmd", HARDLINE)
def test_hardline_denied_even_under_yolo(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perm, "YOLO_FROZEN", True)
    e = PermissionEngine(ALLOW_ALL, mode="yolo")
    d = e.evaluate("bash", cmd)
    assert d.action == "deny" and d.reason == "floor", (cmd, d)


@pytest.mark.parametrize("cmd", SECRET)
def test_secret_scanner_denies_shell_reads(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perm, "YOLO_FROZEN", True)
    d = PermissionEngine(ALLOW_ALL, mode="yolo").evaluate("bash", cmd)
    assert d.action == "deny" and d.reason == "floor", (cmd, d)


@pytest.mark.parametrize("cmd", ALLOWED)
def test_no_false_positives(cmd: str) -> None:
    assert floors.analyze(cmd) == [], cmd
    assert PermissionEngine(ALLOW_ALL).evaluate("bash", cmd).action == "allow", cmd


@pytest.mark.parametrize("cmd", ASK)
def test_recoverable_git_always_asks_even_under_yolo(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perm, "YOLO_FROZEN", True)
    d = PermissionEngine(ALLOW_ALL, mode="yolo").evaluate("bash", cmd)
    assert d.action == "ask" and d.reason == "floor", (cmd, d)


def test_ask_floor_passes_an_explicit_rule_or_always_grant() -> None:
    e = PermissionEngine([*ALLOW_ALL, Rule(permission="bash", pattern="git reset --hard*", action="allow")])
    assert e.evaluate("bash", "git reset --hard HEAD").action == "allow"
    assert e.evaluate("bash", "git clean -fd").action == "ask"  # the rule names reset only
    e.grant_always("bash", "git clean -fd")
    assert e.evaluate("bash", "git clean -fd").action == "allow"
    # force push stays a hardline deny whatever the rules say
    e2 = PermissionEngine([Rule(permission="bash", pattern="git push*", action="allow")])
    assert e2.evaluate("bash", "git push --force").action == "deny"


def test_file_tools_share_the_secret_path_rules() -> None:
    e = PermissionEngine([Rule(permission="read", pattern="*", action="allow")])
    for path in ("/w/.env", "/w/prod.env", "/w/.env.local", "/h/.auoreo/secrets.db"):
        assert e.evaluate("read", path).action == "deny", path
    for path in ("/w/.env.example", "/w/.env.sample", "/w/.envrc", "/w/env.py"):
        assert e.evaluate("read", path).action == "allow", path


def test_unbalanced_quotes_still_checked() -> None:
    assert floors.hardline("rm -rf / 'oops") is not None
    assert floors.hardline('cat .env "') is not None


# ------------------------------------------------------------- executor stage
async def run(services: Services, workdir: Path, name: str, args: dict) -> tuple[str, bool]:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_ALL),
    )
    return out.result.content, out.result.is_error


def check_saved(saved: Path, big: str) -> None:
    assert saved.read_text() == big
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    # running the saved file is never a blind retry: it always asks, and its content is checked
    engine = PermissionEngine(ALLOW_ALL)
    assert engine.evaluate("bash", f"bash {saved}").action == "ask"
    saved.write_text("rm -rf /\n")
    assert engine.evaluate("bash", f"bash {saved}").action == "deny"


async def test_oversized_payload_is_saved_not_run(services: Services, workdir: Path) -> None:
    marker = workdir / "ran"
    big = f"touch {marker}\n" + "# pad\n" * 4000
    text, err = await run(services, workdir, "bash", {"command": big})
    assert err and "bash " in text and "saved, not run" in text and not marker.exists()
    saved = Path(text.split("to ", 1)[1].split(". Review", 1)[0])
    assert saved.parent == services.settings.home / "blocked" and saved.name.startswith("blocked-")
    check_saved(saved, big)
    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow))).scalars().all()
    assert any(r.action == "tool.denied" and r.reason == "oversized_payload" for r in rows)


async def test_payload_limit_is_a_setting(services: Services, workdir: Path) -> None:
    services.settings.floors.max_inline_command_bytes = 2048
    text, err = await run(services, workdir, "bash", {"command": "echo " + "a" * 3000})
    assert err and "limit 2048" in text


async def test_monitor_and_stdin_tools_get_the_shell_floors(services: Services, workdir: Path) -> None:
    text, err = await run(services, workdir, "monitor", {"cmd": "cat .env", "until": "x", "timeout_s": 1})
    assert err and "secret-path floor" in text
    text, err = await run(services, workdir, "monitor", {"cmd": "rm -rf /", "until": "x", "timeout_s": 1})
    assert err and "hardline" in text
    text, err = await run(services, workdir, "write_stdin", {"session_id": "nope", "chars": "cat .env\n"})
    assert err and "secret-path floor" in text
    assert services.harness.tools["terminal_pty_send"].manifest.shell_args == ["chars"]
    text, err = await run(services, workdir, "monitor", {"path": ".env", "until": "x", "timeout_s": 1})
    assert err and "secret-path floor" in text


def test_saved_powershell_payload_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.tools.shell_floor import save_blocked

    saved = save_blocked(Path(os.environ["AUOREO_HOME"]), "Write-Output hi", ".ps1")
    assert PermissionEngine(ALLOW_ALL).evaluate("bash", f"pwsh -File {saved}").action == "ask"
