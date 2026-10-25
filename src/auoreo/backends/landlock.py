"""Landlock layer for the Linux sandboxes (spec §19.8, §24.9, §25.10).

The parent never restricts itself: a tiny helper process (`python3 -I -S -c HELPER cfg argv…`)
creates the ruleset, adds read-only roots + writable roots, sets `NO_NEW_PRIVS`, calls
`landlock_restrict_self` and then `execvp`s the real command, so the restriction applies to
the child (and everything it spawns) only. Under bwrap the helper runs *inside* the
namespace (Landlock forbids mount(2) once applied, so it must come after bwrap's setup).

ABI probe: `landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)`; the handled
mask is best-effort per ABI (<2 drops REFER, <3 TRUNCATE, <4 no TCP rules, <5 IOCTL_DEV) and
every rule's `allowed_access` is masked by the handled set. Unavailable → the helper exits
126 when `require` is set (never a silent downgrade), else runs the command unconfined.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

SYS_CREATE_RULESET = 444  # same number on x86_64, aarch64 and the generic syscall table
CREATE_RULESET_VERSION = 1
SYSTEM_RO: tuple[str, ...] = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc", "/opt", "/proc")
SYSTEM_RW: tuple[str, ...] = ("/dev",)


def abi() -> int:
    """Landlock ABI version of the running kernel, or 0 when unavailable (never raises)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        ver = int(libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(1)))
    except (OSError, AttributeError):
        return 0
    return ver if ver > 0 else 0


def describe(version: int | None = None) -> str:
    v = abi() if version is None else version
    if v <= 0:
        return "landlock: unavailable (kernel lacks it or LSM disabled)"
    net = "fs+net(tcp)" if v >= 4 else "fs"
    return f"landlock: ABI {v} ({net})"


# The helper runs in the child before exec; stdlib-only (ctypes), no site imports.
HELPER = r"""
import ctypes, json, os, stat, sys
cfg = json.loads(sys.argv[1]); argv = sys.argv[2:]
libc = ctypes.CDLL(None, use_errno=True)
def die(msg):
    sys.stderr.write("[sandbox: landlock " + msg + "]\n"); sys.exit(126)
abi = libc.syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
if abi < 1:
    if cfg.get("require"):
        die("unavailable: " + os.strerror(ctypes.get_errno()))
    os.execvp(argv[0], argv)
fs = (1 << 13) - 1
if abi >= 2: fs |= 1 << 13
if abi >= 3: fs |= 1 << 14
if abi >= 5: fs |= 1 << 15
FILE_RIGHTS = 1 | 2 | 4 | (1 << 14) | (1 << 15)
READ = 1 | 4 | 8
net = 3 if abi >= 4 and cfg.get("net") == "none" else 0
class Attr(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]
class Beneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", ctypes.c_uint64), ("fd", ctypes.c_int32)]
attr = Attr(fs, net)
rfd = libc.syscall(444, ctypes.byref(attr), ctypes.c_size_t(16 if abi >= 4 else 8), ctypes.c_uint32(0))
if rfd < 0:
    die("create_ruleset failed: " + os.strerror(ctypes.get_errno()))
for path, want in [(p, READ) for p in cfg["ro"]] + [(p, fs) for p in cfg["rw"]]:
    try:
        pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        continue
    allowed = want & fs
    if not stat.S_ISDIR(os.fstat(pfd).st_mode):
        allowed &= FILE_RIGHTS
    if allowed:
        rule = Beneath(allowed, pfd)
        if libc.syscall(445, ctypes.c_int(rfd), ctypes.c_int(1), ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
            die("add_rule failed for " + path + ": " + os.strerror(ctypes.get_errno()))
    os.close(pfd)
if libc.prctl(38, 1, 0, 0, 0) != 0:
    die("cannot set NO_NEW_PRIVS")
if libc.syscall(446, ctypes.c_int(rfd), ctypes.c_uint32(0)) < 0:
    die("restrict_self failed: " + os.strerror(ctypes.get_errno()))
os.close(rfd)
os.execvp(argv[0], argv)
"""


def roots(
    cwd: Path,
    mode: str,
    readable: Sequence[Path] = (),
    writable: Sequence[Path] = (),
    *,
    tmp: str | None = None,
) -> tuple[list[str], list[str]]:
    """`(read_only, read_write)` hierarchies for one execution (self-sufficient paths only)."""
    ro = [*SYSTEM_RO, sys.base_prefix, sys.prefix, *map(str, readable)]
    rw = [*SYSTEM_RW, tmp or tempfile.gettempdir()]
    if mode == "read-only":
        ro.append(str(cwd))
    else:
        rw += [str(cwd), *map(str, writable)]
    return sorted(set(ro)), sorted(set(rw))


def wrap(
    argv: Sequence[str],
    *,
    cwd: Path,
    mode: str,
    net: str = "none",
    readable: Sequence[Path] = (),
    writable: Sequence[Path] = (),
    python: str | None = None,
    tmp: str | None = None,
    require: bool = True,
) -> list[str]:
    """`argv` prefixed with the Landlock helper (pure; unit-tested)."""
    ro, rw = roots(cwd, mode, readable, writable, tmp=tmp)
    cfg = json.dumps({"ro": ro, "rw": rw, "net": net, "require": require}, separators=(",", ":"))
    return [python or sys.executable, "-I", "-S", "-c", HELPER, cfg, *argv]


def system_python() -> str | None:
    """An interpreter reachable inside bwrap (only /usr and /bin are bound there)."""
    for candidate in ("/usr/bin/python3", "/bin/python3"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None
