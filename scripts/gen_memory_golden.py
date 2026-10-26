"""Generate the memory golden set `evals/memory/{facts,golden}.jsonl` (spec §25.8 L996).

Deterministic and LLM-free: rows come from `tests/fixtures/memory_golden.jsonl` (the hand
written v1 rows) plus template families over fixed vocabularies:

- `prefs`         user preferences (`type: user`, scope user) with near-miss distractors;
- `conventions`   project conventions (`type: project`, scope project);
- `temporal`      a fact superseded by a newer one (old row has `valid_to` before `as_of`:
                  only the new fact is allowed);
- `contradiction` a pair where the newer statement contradicts the older (the older one is
                  superseded; only the newer is allowed);
- `scope`         a user-scope fact with a `session`-scoped twin owned by another session
                  (only the user-scope fact is allowed);
- `legacy`        the v1 fixture rows.

Each golden row is `{id, query, allowed_fact_ids, as_of, scope, category}`; `as_of` is the
date the expectation holds (the eval runs "now", after every `valid_from`). Re-run after
editing the vocabularies; `tests/unit/test_memory_golden.py` checks the files are current.

    uv run python scripts/gen_memory_golden.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "evals" / "memory"
LEGACY = REPO / "tests" / "fixtures" / "memory_golden.jsonl"
AS_OF = "2026-10-01"
OLD_FROM, OLD_TO, NEW_FROM = "2025-03-01", "2026-02-01", "2026-02-01"

PREFS = [
    ("code editor", "Neovim", "VS Code"),
    ("terminal emulator", "WezTerm", "iTerm2"),
    ("login shell", "fish", "zsh"),
    ("browser", "Firefox", "Chrome"),
    ("note taking app", "Obsidian", "Notion"),
    ("keyboard layout", "Colemak", "QWERTY"),
    ("window manager", "Sway", "i3"),
    ("git client", "lazygit", "GitKraken"),
    ("password manager", "Bitwarden", "LastPass"),
    ("music player", "Spotify", "Apple Music"),
    ("coffee order", "flat white", "espresso"),
    ("meeting tool", "Jitsi", "Zoom"),
    ("diagram tool", "Excalidraw", "Visio"),
    ("color theme", "Gruvbox", "Solarized"),
    ("font for coding", "JetBrains Mono", "Fira Code"),
    ("calendar app", "Fantastical", "Outlook"),
    ("mail client", "Thunderbird", "Apple Mail"),
    ("task tracker", "Linear", "Jira"),
    ("laptop", "ThinkPad X1", "MacBook Air"),
    ("phone platform", "Android", "iOS"),
    ("programming font size", "14 point", "12 point"),
    ("test runner", "pytest", "unittest"),
    ("package manager", "uv", "pip"),
    ("linter", "ruff", "flake8"),
    ("chat app", "Signal", "WhatsApp"),
    ("cloud provider", "Hetzner", "AWS"),
    ("database", "PostgreSQL", "MySQL"),
    ("container runtime", "Podman", "Docker"),
    ("CI service", "GitHub Actions", "CircleCI"),
    ("reading device", "Kobo", "Kindle"),
]
PREF_FORMS = [
    ("The user prefers {v} as their {t}.", "which {t} does the user prefer"),
    ("For their {t}, the user always picks {v}.", "what does the user pick for their {t}"),
]
CONVENTIONS = [
    ("commit messages", "the Conventional Commits format"),
    ("Python formatting", "ruff format with a 110 character line length"),
    ("branch names", "the feature/<ticket>-<slug> pattern"),
    ("database migrations", "one Alembic revision per pull request"),
    ("API errors", "RFC 7807 problem details"),
    ("feature flags", "the flags.yaml registry reviewed weekly"),
    ("log lines", "structured JSON with a request id"),
    ("dates in docs", "ISO 8601 absolute dates"),
    ("dependency upgrades", "a Renovate batch every Monday"),
    ("release tags", "semantic versioning with a v prefix"),
    ("secrets", "the keyring, never committed env files"),
    ("code review", "two approvals for changes under payments/"),
    ("frontend state", "React context instead of Redux"),
    ("test fixtures", "factories under tests/factories"),
    ("time zones", "UTC everywhere in storage"),
    ("SQL style", "lowercase keywords and snake_case tables"),
    ("docstrings", "Google style with Args and Returns"),
    ("error budgets", "a 99.9 percent monthly availability target"),
    ("on-call handover", "a written summary in the ops channel"),
    ("container images", "distroless bases pinned by digest"),
    ("translations", "gettext catalogs under locale/"),
    ("benchmarks", "pytest-benchmark with a stored baseline"),
    ("API versioning", "a /v2 prefix for breaking changes"),
    ("design tokens", "the tokens.json file generated from Figma"),
    ("access reviews", "a quarterly audit of admin roles"),
    ("incident reports", "a blameless postmortem within five days"),
    ("cron schedules", "jittered minutes, never on the hour"),
    ("shell scripts", "set -euo pipefail at the top"),
    ("pull request size", "under 400 changed lines"),
    ("config files", "TOML instead of YAML for new tools"),
]
CONV_FORMS = [
    ("In this project, {t} follow {r}.", "what convention do we use for {t}"),
    ("Team rule: {t} must use {r}.", "how should {t} be done here"),
]
SERVICES = [
    ("billing", "eu-west-1", "eu-central-1"),
    ("search", "us-east-1", "us-west-2"),
    ("checkout", "ap-south-1", "ap-southeast-1"),
    ("notifications", "eu-north-1", "eu-west-3"),
    ("analytics", "us-east-2", "ca-central-1"),
    ("auth", "eu-west-2", "eu-south-1"),
    ("media", "sa-east-1", "us-east-1"),
    ("reporting", "ap-northeast-1", "ap-northeast-3"),
    ("ledger", "eu-central-2", "eu-west-1"),
    ("inventory", "us-west-1", "us-east-2"),
    ("recommendations", "ap-east-1", "ap-southeast-2"),
    ("gateway", "me-south-1", "eu-central-1"),
    ("exports", "af-south-1", "eu-west-2"),
    ("scheduler", "ca-west-1", "us-west-2"),
    ("payouts", "eu-west-3", "eu-north-1"),
    ("geocoding", "ap-south-2", "ap-south-1"),
    ("webhooks", "us-gov-west-1", "us-east-1"),
    ("catalog", "il-central-1", "eu-central-1"),
    ("profiles", "mx-central-1", "us-east-2"),
    ("chat", "ap-southeast-3", "ap-northeast-2"),
]
TEMPORAL_FORMS = [
    (
        "The {s} service is deployed to {a}.",
        "The {s} service is now deployed to {b}.",
        "where is the {s} service deployed",
    ),
    (
        "The {s} team owns the {a} rollout plan.",
        "The {s} team now owns the {b} rollout plan.",
        "which rollout plan does the {s} team own",
    ),
]
TEAMS = [
    ("platform", "9:30", "10:15"),
    ("payments", "10:00", "11:00"),
    ("growth", "9:00", "9:45"),
    ("mobile", "11:30", "13:00"),
    ("data", "14:00", "15:30"),
    ("security", "8:45", "9:15"),
    ("design", "16:00", "10:30"),
    ("support", "8:00", "8:30"),
    ("infra", "13:30", "14:30"),
    ("research", "15:00", "16:30"),
    ("sales", "9:15", "10:45"),
    ("legal", "12:00", "12:30"),
    ("finance", "10:30", "11:45"),
    ("hiring", "17:00", "9:30"),
    ("devrel", "11:00", "14:15"),
    ("qa", "9:45", "16:15"),
    ("ml", "10:45", "13:45"),
    ("ops", "7:30", "8:15"),
    ("web", "13:00", "15:15"),
    ("api", "14:45", "11:15"),
]
SCOPED = [
    ("deploy freeze window", "Fridays after 3pm"),
    ("staging database host", "db-stg-2.internal"),
    ("on-call pager tool", "PagerDuty"),
    ("VPN endpoint", "vpn.corp.example"),
    ("design review day", "Wednesday"),
    ("expense limit", "200 euros without approval"),
    ("support SLA", "four business hours"),
    ("default branch", "main"),
    ("artifact registry", "registry.example.internal"),
    ("load test tool", "k6"),
    ("status page", "status.example.com"),
    ("demo environment", "demo-3.example.dev"),
    ("feature flag service", "Unleash"),
    ("error tracker", "Sentry"),
    ("metrics backend", "VictoriaMetrics"),
    ("tracing backend", "Tempo"),
    ("docs site", "docs.example.dev"),
    ("ticket prefix", "OPS-"),
    ("release captain", "Mira"),
    ("backup schedule", "every six hours"),
]


class Builder:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self._by_text: dict[str, str] = {}

    def fact(self, content: str, **meta: Any) -> str:
        if content in self._by_text:
            return self._by_text[content]
        fid = f"f{len(self.facts) + 1:04d}"
        self.facts.append({"id": fid, "content": content, **meta})
        self._by_text[content] = fid
        return fid

    def row(self, query: str, allowed: list[str], category: str, scope: str = "user", **extra: Any) -> None:
        rid = f"q{len(self.rows) + 1:04d}"
        self.rows.append(
            {"id": rid, "query": query, "allowed_fact_ids": allowed, "as_of": AS_OF, "scope": scope}
            | {"category": category, **extra}
        )


def build() -> Builder:
    b = Builder()
    for i, (thing, value, other) in enumerate(PREFS):
        for fact_form, query_form in PREF_FORMS[: 2 if i < 30 else 1]:
            fid = b.fact(fact_form.format(v=value, t=thing), type="user", scope="user")
            b.fact(f"The user once tried {other} for a while but did not keep it.", type="user", scope="user")
            b.row(query_form.format(t=thing), [fid], "prefs")
    for topic, rule in CONVENTIONS:
        for fact_form, query_form in CONV_FORMS:
            fid = b.fact(fact_form.format(t=topic, r=rule), type="project", scope="project")
            b.row(query_form.format(t=topic), [fid], "conventions", scope="project")
    for svc, a, c in SERVICES:
        for old_form, new_form, query_form in TEMPORAL_FORMS:
            old = b.fact(
                old_form.format(s=svc, a=a),
                type="project",
                scope="project",
                valid_from=OLD_FROM,
                valid_to=OLD_TO,
            )
            new = b.fact(
                new_form.format(s=svc, b=c),
                type="project",
                scope="project",
                valid_from=NEW_FROM,
                supersedes=old,
            )
            b.row(query_form.format(s=svc), [new], "temporal", scope="project")
    for team, t1, t2 in TEAMS:
        old = b.fact(
            f"The {team} team's daily standup is at {t1}.",
            type="project",
            scope="team",
            valid_from=OLD_FROM,
            valid_to=OLD_TO,
        )
        new = b.fact(
            f"The {team} team's daily standup is no longer at {t1}; it is at {t2}.",
            type="project",
            scope="team",
            valid_from=NEW_FROM,
            supersedes=old,
            contradicts=old,
        )
        b.row(f"when is the {team} team's daily standup", [new], "contradiction", scope="team")
    for thing, value in SCOPED:
        fid = b.fact(f"The team's {thing} is {value}.", type="reference", scope="user")
        b.fact(
            f"Scratch note: the {thing} for my experiment is {value} (temporary).",
            type="project",
            scope="session",
            owner_session="other-session",
        )
        b.row(f"what is the team's {thing}", [fid], "scope", session_id="eval-session")
    for line in LEGACY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("fact"):
            continue
        fid = b.fact(r["fact"], type="user", scope="user")
        for text in [*r.get("bridge", []), *r.get("distractors", [])]:
            b.fact(text, type="user", scope="user")
        b.row(r["query"], [fid], "legacy", subset=r.get("subset", "base"))
    return b


def dumps(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the committed files are stale")
    args = ap.parse_args(argv)
    b = build()
    files = {OUT / "facts.jsonl": dumps(b.facts), OUT / "golden.jsonl": dumps(b.rows)}
    if args.check:
        stale = [
            p.name for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"stale: {', '.join(stale)} — run scripts/gen_memory_golden.py", file=sys.stderr)
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    print(f"wrote {len(b.rows)} golden rows over {len(b.facts)} facts to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
