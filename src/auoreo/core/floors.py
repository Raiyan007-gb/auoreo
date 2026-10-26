"""Shell-command floors (spec §19.4, §19.7, §24.9, §25.10): argv analysis, not one regex.

`analyze(command)` splits a command line with `shlex` into simple commands (`;`, `&&`,
`||`, `|`, `&`, newlines, `( … )`, `$( … )` and backticks), unwraps launchers (`sudo`,
`doas`, `env`, `nice`, `nohup`, `timeout`, `command`, `exec`, `xargs`, leading `VAR=value`
assignments, `/bin/` prefixes), recurses into `bash -c '…'` / `eval …` (bounded depth) and
then checks each argv:

* **deny** (hardline): recursive `rm` of `/`, `~`, `$HOME` or a system directory, any
  `--no-preserve-root`; `find <system dir> -delete|-exec rm`; `mkfs*`/`mke2fs`/`mkswap`/
  `wipefs`; `dd of=/dev/<device>`; `> /dev/<block device>`; fork bombs; `kill -1`;
  shutdown/reboot/halt/poweroff/`init 0|6`/`systemctl poweroff|reboot|halt|kexec`;
  `chmod|chown -R … /`; `sudo -S|-A|--stdin|--askpass`; `docker|podman --privileged`;
  `git push --force|-f|+refspec`.
* **deny** (secret scanner, §19.7 — tools never read `.env` / `secrets.db`): any argv
  token or redirect target naming `.env`, `.env.<x>` (except `.example|.sample|.template`),
  `<name>.env`, `secrets.db*`, `<AUOREO_HOME>/secrets*`, the Fernet key file,
  `/proc/<pid>/environ`; keyring dumps (`keyring get`, `secret-tool lookup|search`,
  `security find-*-password|dump-keychain`); `printenv <SECRET_NAME>` / `echo $SECRET_NAME`;
  `ps e…` (process environments); `env|printenv|export -p|set|declare` dumps filtered for
  secret names. `.envrc` is *not* a secret path (spec §19.7 lists `*.env`/`*.env.*` only);
  bare `env`/`printenv` dumps stay allowed because the child env is scrubbed and the
  terminal ENV redaction pass masks values.
* **ask** (always, even under yolo; an explicit allow rule naming the command passes):
  `git reset --hard`, `checkout .|-f`, `restore .`, `clean -f`, `branch -D`,
  `push --force-with-lease|--delete`, `commit --amend`, `--no-verify`, `--no-gpg-sign`,
  `git config <write>`, and `bash <file>` for a payload saved under `<home>/blocked/`.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from auoreo.core.floors_secrets import is_secret_path as is_secret_path  # re-export
from auoreo.core.floors_secrets import scan

FloorAction = Literal["deny", "ask"]
MAX_DEPTH = 3
MAX_SCRIPT_BYTES = 1 << 20

_DIRS = "/ /bin /sbin /boot /dev /etc /home /lib /lib32 /lib64 /libx32 /opt /proc /root /run /srv /sys"
_MORE = "/usr /var /mnt /media /snap /usr/bin /usr/sbin /usr/lib /usr/local /var/lib /System /Users /private"
SYSTEM_DIRS = frozenset(f"{_DIRS} {_MORE} /Library /Applications".split())  # noqa: SIM905
HOME_ALIASES = frozenset({"~", "$HOME", "${HOME}", "~root"})
_SAFE_DEV = re.compile(r"^/dev/(null|zero|full|u?random|stdout|stderr|stdin|tty|fd/\d+)$")
_BLOCK_DEV = re.compile(r"^/dev/(sd|hd|vd|xvd|nvme|mmcblk|disk|md|dm-|mapper/|loop)")
_FORK_BOMB = re.compile(r"(\w+|:)\s*\(\)\s*\{[^}]*\1\s*\|\s*&?\s*\1\s*&[^}]*\}")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = frozenset(";&|()")
_REDIRECTS = frozenset({">", ">>", "<", "<>", ">|", "&>", "&>>", "<<<", ">&", "<&"})
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish"})
_PWSH = frozenset({"pwsh", "powershell"})


# Regex backstop over normalized variants (catches text the lexer cannot structure).
BACKSTOP: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+(-[a-z]*\s+)*(/|/\*|~|~/|\$HOME)(\s|$)",
        r"\bmkfs(\.\w+)?\b",
        r"\bdd\b[^|;&]*\bof=/dev/(sd|hd|vd|xvd|nvme|mmcblk|disk)",
        r">\s*/dev/(sd|nvme|hd|disk)",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        r"\bkill\s+(-9\s+|-KILL\s+|-s\s+KILL\s+)?-1\s*($|[;&|])",
        r"\b(shutdown|reboot|halt|poweroff)\b",
        r"\binit\s+[06]\b",
        r"\bsystemctl\s+(poweroff|reboot|halt|kexec)\b",
        r"\bsudo\s+(-[a-zA-Z]*S|--stdin|-[a-zA-Z]*A)\b",
        r"\bchmod\s+(-R\s+)?[0-7]*777\s+/(\s|$)",
        r"\bdocker\b[^|;&]*\s--privileged\b",  # docker never runs privileged (§25.10)
        r"\bpodman\b[^|;&]*\s--privileged\b",
    )
)


@dataclass(frozen=True)
class Finding:
    action: FloorAction
    reason: str
    marker: str = ""
    """For `ask`: the literal command prefix an explicit allow rule must name to pass."""


# --------------------------------------------------------------------------- lexing
def _lex(text: str) -> list[str]:
    lex = shlex.shlex(text.replace("\n", " ; ").replace("`", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split (still checked)
        return re.sub(r"[;&|()`]", " ; ", text).replace("'", " ").replace('"', " ").split()


def _split(tokens: list[str]) -> Iterator[list[str]]:
    cur: list[str] = []
    for tok in tokens:
        if tok and set(tok) <= _SEPARATORS:
            if cur:
                yield cur
            cur = []
        else:
            cur.append(tok)
    if cur:
        yield cur


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1] if "/" in word else word


# ------------------------------------------------------------------- unwrapping
_SUDO_VALUE_OPTS = frozenset("ugCDhprtUT")
_ENV_VALUE_OPTS = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})


def _skip_opts(argv: list[str], i: int, value_opts: frozenset[str] = frozenset()) -> int:
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "-":
        if argv[i] == "--":
            return i + 1
        i += 2 if argv[i] in value_opts else 1
    return i


def _sudo(argv: list[str], findings: list[Finding]) -> list[str] | None:
    i = 1
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        flag = argv[i]
        if flag in {"--stdin", "--askpass"} or (not flag.startswith("--") and ({"S", "A"} & set(flag[1:]))):
            findings.append(Finding("deny", "sudo -S/-A password pipe (run it outside the agent)"))
        i += 2 if (len(flag) == 2 and flag[1] in _SUDO_VALUE_OPTS) else 1
    return argv[i + (1 if i < len(argv) and argv[i] == "--" else 0) :]


def _env(argv: list[str], findings: list[Finding]) -> list[str] | None:
    i = _skip_opts(argv, 1, _ENV_VALUE_OPTS)
    while i < len(argv) and _ASSIGN.match(argv[i]):
        i += 1
    return argv[i:] if i < len(argv) else None  # None: a bare `env` dump


def _launcher(argv: list[str], findings: list[Finding]) -> list[str] | None:
    return argv[_skip_opts(argv, 1, frozenset({"-n", "-c", "-I", "-L", "-P", "-d", "-a"})) :]


def _positional(argv: list[str], findings: list[Finding]) -> list[str] | None:
    return argv[_skip_opts(argv, 1, frozenset({"-s", "--signal", "-k", "--kill-after"})) + 1 :]


_UNWRAP: dict[str, Callable[[list[str], list[Finding]], list[str] | None]] = {
    "sudo": _sudo,
    "doas": _sudo,
    "env": _env,
    "timeout": _positional,  # timeout <duration> cmd
    "chroot": _positional,  # chroot <dir> cmd
    "busybox": lambda argv, _f: argv[1:],
    **dict.fromkeys(
        ("nice", "ionice", "stdbuf", "nohup", "time", "command", "exec", "builtin", "xargs"), _launcher
    ),
}
_KEYWORDS = frozenset({"{", "}", "!", "then", "do", "else"})


def _unwrap(argv: list[str], findings: list[Finding]) -> list[str]:
    """Strip launchers / assignments / keywords until the real program is argv[0]."""
    for _ in range(16):
        while argv and (_ASSIGN.match(argv[0]) or argv[0] in _KEYWORDS):
            argv = argv[1:]
        if not argv:
            return argv
        unwrap = _UNWRAP.get(_base(argv[0]))
        if unwrap is None:
            return [_base(argv[0]), *argv[1:]]
        nxt = unwrap(argv, findings)
        if nxt is None:
            return [_base(argv[0])]
        argv = nxt
    return argv


# ----------------------------------------------------------------------- paths
def _norm(path: str) -> str:
    p = re.sub(r"/+", "/", path.strip())
    for suffix in ("/*", "/.", "/"):
        while len(p) > 1 and p.endswith(suffix):
            p = p[: -len(suffix)] or "/"
    return p


def _protected(path: str) -> bool:
    p = _norm(path)
    if p in SYSTEM_DIRS or p in HOME_ALIASES or p in {"*", "/*"}:
        return True
    if p.startswith(("~/", "$HOME/", "${HOME}/")) and _norm(p.split("/", 1)[1]) in {"", "*", "."}:
        return True
    home = os.path.expanduser("~").rstrip("/")
    return bool(home) and p == home


# ---------------------------------------------------------------------- checks
Check = Callable[[list[str]], list[Finding]]


def _deny(reason: str) -> list[Finding]:
    return [Finding("deny", reason)]


def _rm(argv: list[str]) -> list[Finding]:
    if "--no-preserve-root" in argv:
        return _deny("rm --no-preserve-root (hardline floor)")
    end = argv.index("--") if "--" in argv else len(argv)
    flags = [a for a in argv[1:end] if a.startswith("-") and a != "-"]
    targets = [a for a in argv[1:end] if a not in flags] + argv[end + 1 :]
    recursive = "--recursive" in flags or any(
        not f.startswith("--") and bool({"r", "R"} & set(f[1:])) for f in flags
    )
    if recursive and any(_protected(t) for t in targets):
        return _deny("recursive rm of the root, home or a system directory (hardline floor)")
    return []


def _find(argv: list[str]) -> list[Finding]:
    roots = []
    for arg in argv[1:]:
        if arg.startswith(("-", "(", "!")):
            break
        roots.append(arg)
    rest = argv[1 + len(roots) :]
    destructive = "-delete" in rest or any(
        a in {"-exec", "-execdir", "-ok"} and i + 1 < len(rest) and _base(rest[i + 1]) in {"rm", "shred"}
        for i, a in enumerate(rest)
    )
    if destructive and any(_protected(r) for r in roots or ["."]):
        return _deny("find -delete/-exec rm over the root, home or a system directory (hardline floor)")
    return []


def _dd(argv: list[str]) -> list[Finding]:
    for arg in argv[1:]:
        if arg.startswith("of=/dev/") and not _SAFE_DEV.match(arg[3:]):
            return _deny("dd onto a device (hardline floor)")
    return []


def _kill(argv: list[str]) -> list[Finding]:
    return _deny("kill -1 signals every process (hardline floor)") if argv[-1:] == ["-1"] else []


def _recursive_root(argv: list[str]) -> list[Finding]:
    recursive = any(a in {"-R", "--recursive"} or (a.startswith("-") and "R" in a[1:]) for a in argv[1:])
    if recursive and any(_norm(a) == "/" or _norm(a) in SYSTEM_DIRS for a in argv[1:] if a[:1] in "/~$"):
        return _deny(f"recursive {argv[0]} of the root or a system directory (hardline floor)")
    return []


def _power(argv: list[str]) -> list[Finding]:
    return _deny(f"{argv[0]} (hardline floor)")


def _init(argv: list[str]) -> list[Finding]:
    return _deny("init 0/6 (hardline floor)") if argv[1:2] in (["0"], ["6"]) else []


def _systemctl(argv: list[str]) -> list[Finding]:
    verbs = {"poweroff", "reboot", "halt", "kexec", "suspend", "hibernate"}
    return _deny("systemctl power action (hardline floor)") if verbs & set(argv[1:]) else []


def _container(argv: list[str]) -> list[Finding]:
    if any(a == "--privileged" or a.startswith("--privileged=") for a in argv[1:]):
        return _deny(f"{argv[0]} --privileged (hardline floor)")
    return []


def _shred(argv: list[str]) -> list[Finding]:
    return _deny("shred of a device (hardline floor)") if any(_BLOCK_DEV.match(a) for a in argv[1:]) else []


def _mkfs(argv: list[str]) -> list[Finding]:
    return _deny(f"{argv[0]} formats a filesystem (hardline floor)")


_GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})


def _git(argv: list[str]) -> list[Finding]:
    i = _skip_opts(argv, 1, _GIT_VALUE_OPTS)
    if i >= len(argv):
        return []
    sub, args = argv[i], argv[i + 1 :]
    out: list[Finding] = []
    if "--no-verify" in args or (sub == "commit" and any(_short(a, "n") for a in args)):
        out.append(Finding("ask", "git --no-verify skips hooks", f"git {sub}"))
    if "--no-gpg-sign" in args:
        out.append(Finding("ask", "git --no-gpg-sign", f"git {sub}"))
    handler = _GIT_SUBS.get(sub)
    return out + (handler(args) if handler else [])


def _short(arg: str, flag: str) -> bool:
    return arg.startswith("-") and not arg.startswith("--") and flag in arg[1:]


def _git_push(args: list[str]) -> list[Finding]:
    if "--force" in args or any(_short(a, "f") for a in args) or any(a.startswith("+") for a in args):
        return _deny("git push --force rewrites remote history (hardline floor)")
    if any(a.startswith(("--force-with-lease", "--force-if-includes", "--mirror")) for a in args):
        return [Finding("ask", "git force-with-lease push", "git push --force-with-lease")]
    if "--delete" in args or any(_short(a, "d") for a in args) or any(a.startswith(":") for a in args):
        return [Finding("ask", "git push deletes a remote ref", "git push --delete")]
    return []


def _git_ask(sub: str, test: Callable[[list[str]], bool], what: str, marker: str) -> Check:
    def check(args: list[str]) -> list[Finding]:
        return [Finding("ask", f"destructive git: {what}", marker)] if test(args) else []

    return check


def _config_write(args: list[str]) -> bool:
    reads = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--get-urlmatch"}
    positional = [a for a in args if not a.startswith("-")]
    return not (reads & set(args)) and len(positional) >= 2 or "--unset" in args or "--add" in args


_GIT_SUBS: dict[str, Check] = {
    "push": _git_push,
    "reset": _git_ask("reset", lambda a: "--hard" in a, "reset --hard", "git reset --hard"),
    "checkout": _git_ask(
        "checkout",
        lambda a: "." in a or "--force" in a or any(_short(x, "f") for x in a),
        "checkout . / -f discards changes",
        "git checkout",
    ),
    "restore": _git_ask("restore", lambda a: "." in a, "restore . discards changes", "git restore"),
    "clean": _git_ask(
        "clean", lambda a: "--force" in a or any(_short(x, "f") for x in a), "clean -f", "git clean"
    ),
    "branch": _git_ask(
        "branch",
        lambda a: "-D" in a or ("--delete" in a and "--force" in a) or any(_short(x, "D") for x in a),
        "branch -D",
        "git branch -D",
    ),
    "commit": _git_ask("commit", lambda a: "--amend" in a, "commit --amend", "git commit --amend"),
    "config": _git_ask("config", _config_write, "git config write", "git config"),
}

_CHECKS: dict[str, Check] = {
    "rm": _rm,
    "find": _find,
    "dd": _dd,
    "kill": _kill,
    "chmod": _recursive_root,
    "chown": _recursive_root,
    "shutdown": _power,
    "reboot": _power,
    "halt": _power,
    "poweroff": _power,
    "init": _init,
    "telinit": _init,
    "systemctl": _systemctl,
    "docker": _container,
    "podman": _container,
    "shred": _shred,
    "mke2fs": _mkfs,
    "mkswap": _mkfs,
    "wipefs": _mkfs,
    "git": _git,
}


# ----------------------------------------------------------------------- driver
def _blocked_script(argv: list[str]) -> Path | None:
    from auoreo.core.paths import home_dir

    blocked = (home_dir() / "blocked").resolve()
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        try:
            p = Path(os.path.expanduser(arg)).resolve()
        except (OSError, RuntimeError):
            return None
        return p if blocked in p.parents and p.is_file() else None
    return None


def _shell_script(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv[1:], start=1):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return argv[i + 1] if i + 1 < len(argv) else ""
        if not arg.startswith("-"):
            return None
    return None


def _command(argv: list[str], raw: str, depth: int, out: list[Finding]) -> None:
    redirects, plain = [], []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _REDIRECTS or (tok[:-1].isdigit() and tok[-1:] in "<>" and len(tok) > 1):
            if i + 1 < len(argv):
                redirects.append(argv[i + 1])
            i += 2
            continue
        if ("$(" in tok or "<(" in tok or ">(" in tok) and depth < MAX_DEPTH:
            out.extend(analyze(tok.split("(", 1)[1], depth + 1))
        plain.append(tok)
        i += 1
    if any(_BLOCK_DEV.match(r) for r in redirects):
        out.append(Finding("deny", "redirect onto a block device (hardline floor)"))
    argv = _unwrap(plain, out)
    out.extend(Finding("deny", why) for why in scan(argv, redirects, raw))
    if not argv:
        return
    name = argv[0]
    if name in SHELLS | _PWSH and depth < MAX_DEPTH:
        script = None if name in _PWSH else _shell_script(argv)
        if script is not None:
            out.extend(analyze(script, depth + 1))
        elif (saved := _blocked_script(argv)) is not None:
            out.append(Finding("ask", "runs a saved oversized payload; review it first", f"{name} {saved}"))
            if saved.stat().st_size <= MAX_SCRIPT_BYTES:
                out.extend(analyze(saved.read_text(errors="replace"), depth + 1))
    elif name == "eval" and depth < MAX_DEPTH:
        out.extend(analyze(" ".join(argv[1:]), depth + 1))
    check = _CHECKS.get(name) or (_mkfs if name.startswith("mkfs") else None)
    if check is not None:
        out.extend(check(argv))


def analyze(command: str, depth: int = 0) -> list[Finding]:
    """Every floor finding for a shell command line (empty = no floor applies)."""
    out: list[Finding] = []
    if _FORK_BOMB.search(command):
        out.append(Finding("deny", "fork bomb (hardline floor)"))
    for argv in _split(_lex(command)):
        _command(argv, command, depth, out)
    return out


def hardline(command: str) -> str | None:
    """The first deny reason for this command, or None."""
    return next((f.reason for f in analyze(command) if f.action == "deny"), None)


def ask_floor(command: str) -> Finding | None:
    """The first always-ask finding (destructive-but-recoverable git, saved payloads)."""
    return next((f for f in analyze(command) if f.action == "ask"), None)
