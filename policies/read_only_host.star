# description: Example (off by default): on SSH hosts tagged `readonly`, deny write_stdin, uploads and commands outside a read-only allowlist. A filter on model-written text, not enforcement.
# priority: 600
# enabled: false

# How to use: copy this file to <home>/policies/, set `enabled: true`, tag a host
# (`tags: [readonly]` in hosts/<name>.yaml) and turn on settings.m7.ssh.policy_fields.
#
# This filters the text the model writes. It is NOT a security boundary: an allowlisted
# program can still write (`find -delete`, a pager's shell escape, an alias on the host).
# Real read-only access is a remote account without write permission.
#
# `req["host"]` is None unless the call acts on an SSH environment (exec_command,
# write_stdin, env_transfer, use_environment); local tools (bash, edit, ...) never see it.

_ALLOW = [
    "cat", "ls", "head", "tail", "grep", "egrep", "wc", "df", "du", "free", "uptime",
    "ps", "pgrep", "id", "whoami", "hostname", "uname", "date", "stat", "file", "echo",
    "journalctl", "dmesg", "ss", "netstat", "pwd", "which", "true",
]
_ALLOW_TWO = [
    "systemctl status", "systemctl is-active", "systemctl list-units", "docker ps",
    "docker logs", "docker inspect", "git status", "git log", "git diff", "git show",
    "kubectl get", "kubectl describe", "kubectl logs",
]
_SEPARATORS = ["&&", "||", ";", "|", "&", "\n"]


def _readonly(req):
    host = req["host"]
    return host != None and "readonly" in host["tags"]


def _segments(cmd):
    for sep in _SEPARATORS:
        cmd = cmd.replace(sep, "\n")
    return [s.strip() for s in cmd.split("\n") if s.strip()]


def _allowed(segment):
    words = segment.split()
    if words[0] in _ALLOW:
        return True
    return len(words) > 1 and (words[0] + " " + words[1]) in _ALLOW_TWO


def decide(req):
    if not _readonly(req):
        return None
    if req["tool"] == "write_stdin":
        return "deny"
    if req["permission"] == "sftp" and ":upload:" in req["pattern"]:
        return "deny"
    if req["tool"] == "exec_command" and req["permission"] == "bash":
        cmd = req["pattern"]
        if re_match(r"[<>`]|\$\(", cmd):
            return "deny"  # redirection, command substitution
        for segment in _segments(cmd):
            if not _allowed(segment):
                return "deny"
    return None
