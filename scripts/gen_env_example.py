"""Generate `.env.example` from the shared variable catalog (`auoreo.core.env_catalog_data`).

    uv run python scripts/gen_env_example.py --check   # CI: fail when .env.example drifts
    uv run python scripts/gen_env_example.py --write   # after adding a setting or a key

The catalog is the same data the web's Settings → Environment page shows. Tags come from the
real loader (`core.dotenv`), so `[home]` / `[env-only]` can never disagree with what a project
`.env` is actually allowed to set.
"""

from __future__ import annotations

import sys
from pathlib import Path

from auoreo.core.env_catalog import FIELDS, default_of, json_kind, tier_tag
from auoreo.core.env_catalog_data import ITEMS, Done, K, S, Sec, T

done: set[str] = set()
out: list[str] = []
w = out.append
TAGS = {"env-only": "  [env-only]", "home": "  [home]", "": ""}


def setting(name: str, note: str = "", value: str = "") -> None:
    assert name in FIELDS, name
    assert name not in done, name
    done.add(name)
    env = f"AUOREO_{name.upper()}"
    tag = TAGS[tier_tag(env)]
    kind = json_kind(name)
    if kind == "JSON object":
        dflt = "JSON object; settings.json key `" + name + "`"
    else:
        dflt = f"default {default_of(name)}" + (" (JSON)" if kind else "")
    line = "# " + " · ".join(p for p in (note, dflt) if p) + tag
    if len(line) > 100:  # keep comments readable: the tag/default go on their own line
        w("# " + note)
        line = "# " + dflt + tag
    w(line)
    w(f"{env}={value}")


def secret(env: str, note: str) -> None:
    w(f"# {note}{TAGS[tier_tag(env)]}")
    w(f"{env}=")


def section(title: str) -> None:
    w("")
    w(f"# {'-' * (90 - len(title) - 3)} {title}")


def body() -> None:
    for item in ITEMS:
        if isinstance(item, Sec):
            section(item.title)
        elif isinstance(item, T):
            w(item.text)
        elif isinstance(item, S):
            setting(item.name, item.note, item.value)
        elif isinstance(item, K):
            secret(item.env, item.note)
        elif isinstance(item, Done):
            done.add(item.name)


def render() -> str:
    body()
    missing = sorted(set(FIELDS) - done)
    if missing:
        raise SystemExit(f"settings fields missing from the catalog: {missing}")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    target = Path(__file__).resolve().parents[1] / ".env.example"
    text = render()
    if "--write" in sys.argv:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target.name}: {len(text.splitlines())} lines, {len(done)} settings fields")
        return 0
    if target.read_text(encoding="utf-8") != text:
        print(f"{target.name} is stale; run: uv run python scripts/gen_env_example.py --write")
        return 1
    print(f"{target.name} is up to date ({len(done)} settings fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
