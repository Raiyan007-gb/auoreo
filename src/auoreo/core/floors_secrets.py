"""Secret scanner for shell commands (spec §19.7, §24.9, §25.10): tools never read `.env` /
`secrets.db`. Used by `core.floors` (shell argv) and `core.permissions` (file-tool paths).

`.envrc` is *not* a secret path (spec §19.7 lists `*.env` / `*.env.*`); `.env.example`,
`.env.sample` and `.env.template` stay readable. Bare `env` / `printenv` dumps stay allowed:
the child env is scrubbed (`core.secrets.child_env`) and the terminal ENV redaction pass masks
values; a dump filtered for secret names (`env | grep KEY`) is refused.
"""

from __future__ import annotations

import fnmatch
import os
import re

ENV_FILE_EXEMPT = (".example", ".sample", ".template")
_PROC_ENVIRON = re.compile(r"/proc/[^/\s]*/environ\b")
_SECRET_NAME = re.compile(r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|DSN|CREDENTIALS?)(?:$|_)")
_FILTERED_DUMP = re.compile(  # `env | grep -i key`: a dump piped into a filter for secret names
    r"(?i)\|\s*(?:\S*/)?(?:grep|egrep|fgrep|rg|awk|sed)\b[^|;&]*(?:key|token|secret|passw|credential)"
)


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1] if "/" in word else word


def _auoreo_home() -> str:
    from auoreo.core.paths import home_dir

    return str(home_dir()).rstrip("/")


def is_secret_path(value: str) -> bool:
    """True for `.env`, `.env.<x>` (not example/sample/template), `<x>.env`, `secrets.db*`,
    `<AUOREO_HOME>/secrets*`, the secrets key file and `/proc/<pid>/environ`."""
    if not value:
        return False
    if _PROC_ENVIRON.search(value):
        return True
    for piece in re.split(r"[=:,]", value):
        piece = piece.strip().rstrip("/")
        if piece and not any(c.isspace() for c in _base(piece)) and _secret_piece(piece):
            return True
    return False


def _secret_piece(piece: str) -> bool:
    name = _base(piece)
    if any(c in name for c in "*?["):
        return _secret_glob(name)
    if name.startswith("secrets.db") or name == ".secrets.key":
        return True
    if name == ".env" or name.endswith(".env"):
        return True
    if name.startswith(".env."):
        return not name.endswith(ENV_FILE_EXEMPT)
    home = _auoreo_home()
    expanded = os.path.expanduser(piece.replace("$AUOREO_HOME", home).replace("${AUOREO_HOME}", home))
    return name.startswith("secrets") and (
        expanded.startswith(home + "/") or "/.auoreo/" in expanded or expanded.startswith(".auoreo/")
    )


def _secret_glob(glob: str) -> bool:
    """`.env*`, `*.env`, `secrets.*`, … — globs that would expand to a secret file."""
    if "env" not in glob.lower() and "secret" not in glob.lower():
        return False
    return any(fnmatch.fnmatchcase(c, glob) for c in (".env", ".env.local", "prod.env", "secrets.db"))


_DATA_ARGS = frozenset({"echo", "printf", "git"})  # args are text, not files (redirects still count)
_GREP = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack"})
_ENV_DUMPS = frozenset({"env", "printenv", "export", "set", "declare", "typeset", "compgen"})


def _path_args(argv: list[str]) -> list[str]:
    name, args = argv[0], argv[1:]
    if name in _GREP:  # the first positional / `-e` value is a pattern, not a file
        out, skipped, i = [], False, 0
        while i < len(args):
            a = args[i]
            if a in {"-e", "--regexp"}:
                i, skipped = i + 2, True
                continue
            if not a.startswith("-") and not skipped:
                skipped = True
            elif not a.startswith("-"):
                out.append(a)
            i += 1
        return out
    if name == "git":  # pathspecs after the subcommand still name files (`git show :.env`)
        return [a for a in args if not a.startswith("-")][1:]
    return [] if name in _DATA_ARGS else args


def scan(argv: list[str], redirects: list[str], raw: str) -> list[str]:
    """Deny reasons for one simple command (`argv` already unwrapped)."""
    name = argv[0] if argv else ""
    targets = [*redirects, *(_path_args(argv) if argv else [])]
    if any(is_secret_path(t) for t in targets) or (name and is_secret_path(name)):
        return ["tools never read or write .env / secrets.db (secret-path floor)"]
    if name == "keyring" and {"get", "--get"} & set(argv[1:]):
        return ["keyring dump (secret floor)"]
    if name == "secret-tool" and {"lookup", "search"} & set(argv[1:]):
        return ["keyring dump (secret floor)"]
    if name == "security" and any("password" in a or a == "dump-keychain" for a in argv[1:]):
        return ["keychain dump (secret floor)"]
    if name == "printenv" and any(_SECRET_NAME.search(a) for a in argv[1:]):
        return ["printenv of a secret variable (secret floor)"]
    if name == "ps" and any(re.fullmatch(r"[a-zA-Z]*e[a-zA-Z]*", a) for a in argv[1:]):
        return ["ps e prints process environments (secret floor)"]
    if name in _ENV_DUMPS and _env_dump(argv) and _FILTERED_DUMP.search(raw):
        return ["environment dump filtered for secrets (secret floor)"]
    return []


def _env_dump(argv: list[str]) -> bool:
    flags = {a for a in argv[1:] if a.startswith("-")}
    positional = [a for a in argv[1:] if not a.startswith("-")]
    by_name: dict[str, bool] = {
        "env": not positional,
        "printenv": not positional,
        "export": not positional or "-p" in flags,
        "set": not argv[1:],
        "declare": bool({"-p", "-x"} & flags) or not argv[1:],
        "typeset": bool({"-p", "-x"} & flags) or not argv[1:],
        "compgen": "-v" in flags or "-e" in flags,
    }
    return by_name.get(argv[0], False)
