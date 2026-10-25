# description: Bundled exec policy: deny download-pipe-to-shell and force-pushes to main/master; review rm -rf outside /tmp.
# priority: 100
# enabled: true

# Deliberately narrow: everything else returns None so the permission rules decide.

_PIPE_TO_SHELL = r"\b(curl|wget)\b[^;&]*\|\s*(sudo\s+(-\S+\s+)*)?(env\s+)?(ba|z|da|k)?sh\b"
_FORCE = r"(\s--force(-with-lease)?(=\S*)?(\s|$)|\s-[a-zA-Z]*f[a-zA-Z]*(\s|$))"
_PROTECTED = r"(^|[\s:+/])(main|master)(\s|$)"
_SEPARATORS = [";", "&", "|", "(", ")", "\n"]

def _segments(cmd):
    for sep in _SEPARATORS:
        cmd = cmd.replace(sep, " ; ")
    out = []
    cur = []
    for tok in cmd.split():
        if tok == ";":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok.strip("'\""))
    if cur:
        out.append(cur)
    return out

def _force_push_protected(seg):
    text = " ".join(seg)
    if not re_match(r"(^|\s)git(\s+-\S+)*\s+push(\s|$)", text):
        return False
    if re_match(r"\s\+(main|master)(\s|$)", text) or re_match(r"\s\+\S*:(refs/heads/)?(main|master)(\s|$)", text):
        return True
    return re_match(_FORCE, text) and re_match(_PROTECTED, text)

def _rm_rf_outside_tmp(seg):
    if not seg or not (seg[0] == "rm" or seg[0].endswith("/rm")):
        return False
    flags = ""
    targets = []
    for tok in seg[1:]:
        if tok == "--recursive":
            flags += "r"
        elif tok == "--force":
            flags += "f"
        elif tok.startswith("-") and not tok.startswith("--"):
            flags += tok[1:]
        elif not tok.startswith("-"):
            targets.append(tok)
    if not (("r" in flags or "R" in flags) and "f" in flags):
        return False
    for t in targets:
        if not t.startswith("/tmp/") or ".." in t:
            return True
    return False

def decide(req):
    if req["permission"] != "bash":
        return None
    cmd = req["pattern"]
    if re_match(_PIPE_TO_SHELL, cmd):
        return "deny"
    review = False
    for seg in _segments(cmd):
        if _force_push_protected(seg):
            return "deny"
        if _rm_rf_outside_tmp(seg):
            review = True
    return "review" if review else None
