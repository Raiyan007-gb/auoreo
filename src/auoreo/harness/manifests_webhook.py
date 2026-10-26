"""`webhooks/<route>.yaml` manifests (spec §15, §19.11), split out of `manifests.py`.

Filters: a leaf `{field, equals|contains|in|regex|exists|in_file}` (every condition set must
hold) or a combinator `{all: [...]}` / `{any: [...]}` / `{not: {...}}` (nestable). `in_file`
names a text file of allowed values (one per line, `#` comments) under `<home>/webhooks/`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME_RE = r"^[a-z][a-z0-9_]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class WebhookFilter(_Strict):
    field: str | None = None
    """Dotted path into the normalized payload, e.g. `action` or `pull_request.draft`."""
    equals: str | None = None
    contains: str | None = None
    in_: list[str] | None = Field(default=None, alias="in")
    """The value (as text) is one of these."""
    in_file: str | None = None
    """The value is a line of `<home>/webhooks/<in_file>` (relative path, no `..`)."""
    regex: str | None = None
    exists: bool | None = None
    all_: list[WebhookFilter] | None = Field(default=None, alias="all")
    any_: list[WebhookFilter] | None = Field(default=None, alias="any")
    not_: WebhookFilter | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _shape(self) -> WebhookFilter:
        combinators = [c for c in (self.all_, self.any_, self.not_) if c is not None]
        if len(combinators) > 1:
            raise ValueError("a filter takes one of all / any / not")
        if combinators and self.field is not None:
            raise ValueError("a combinator filter (all / any / not) has no `field`")
        if not combinators and self.field is None:
            raise ValueError("a filter needs `field` (or one of all / any / not)")
        if self.in_file is not None and (self.in_file.startswith("/") or ".." in self.in_file.split("/")):
            raise ValueError("in_file must be a relative path under <home>/webhooks/ without '..'")
        return self

    @property
    def label(self) -> str:
        if self.field is not None:
            return self.field
        return "all" if self.all_ is not None else "any" if self.any_ is not None else "not"


RAW_VAR_RE = re.compile(r"\{\{\s*__raw__\s*\}\}")


def strip_raw(template: str) -> str:
    """The template without `{{__raw__}}` (the raw body), for strict variable checks."""
    return RAW_VAR_RE.sub("", template)


def filter_leaves(filters: list[WebhookFilter]) -> Iterator[WebhookFilter]:
    """Every leaf filter, through nested all / any / not."""
    for f in filters:
        if f.all_ is not None or f.any_ is not None:
            yield from filter_leaves(list(f.all_ or f.any_ or []))
        elif f.not_ is not None:
            yield from filter_leaves([f.not_])
        else:
            yield f


class WebhookManifest(_Strict):
    """`webhooks/<route>.yaml` → `POST /hooks/<route>`. Verified (HMAC) and enqueued before
    202 is returned; the agent runs in its own session afterwards."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1)
    provider: str = Field(default="generic", pattern=r"^[a-z][a-z0-9_-]*$")
    """github: `X-Hub-Signature-256` body HMAC · generic: V2 `HMAC("<unix-sec>.<body>")` +
    `X-Timestamp` ±300 s · gitlab: `X-Gitlab-Token` · svix: `svix-*` triple (`whsec_` secret) ·
    telegram-callback: `X-Telegram-Bot-Api-Secret-Token` · any other kind must be provided by a
    running plugin (`provides: [webhook_provider]`), else the route answers 503."""
    secret: str = Field(min_length=1)
    """`secret:<name>` reference resolved through the secret store (never the value);
    `<name>#prev` keeps verifying for 24 h after `auoreo webhook rotate`."""
    events: list[str] = Field(default_factory=list)
    """Provider event names to accept (`X-GitHub-Event`, `X-Gitlab-Event`, payload `type`/`event`);
    empty = all."""
    filters: list[WebhookFilter] = Field(default_factory=list)
    agent: str = Field(default="default", pattern=NAME_RE)
    workflow: str | None = None
    prompt: str = Field(min_length=1)
    """Template over `{{event.*}}` (dotted paths, missing → literal), `{{route}}`, `{{delivery_id}}`,
    and `{{__raw__}}` (the raw body, capped at 4000 characters)."""
    deliver: list[str] = Field(default_factory=lambda: ["log"])
    enabled: bool = True
    rate_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    max_body_bytes: int = Field(default=1_048_576, ge=1_024, le=50 * 1_048_576)
    max_steps: int | None = Field(default=None, ge=1, le=500)
    challenge: bool = False
    """Answer provider verification challenges before HMAC: `GET ?challenge=<v>` echo and
    Slack-style `{"type": "url_verification", "challenge": <v>}`."""
    deliver_only: bool = False
    """Deliver the rendered `prompt` straight to `deliver` targets (no agent run): 200 once
    delivered, 502 when every target failed."""
    script: str | None = None
    """Optional transform: `<home>/scripts/<script>` gets the payload as JSON on stdin and prints
    the new payload (a JSON object, or `null` to ignore the delivery). Scrubbed env, no shell,
    timeout `script_timeout_s` (default `teams2.webhook_script_timeout_s`)."""
    script_timeout_s: float | None = Field(default=None, gt=0, le=300)
    allowed_cidrs: list[str] = Field(default_factory=list)
    """Optional IP allowlist (CIDRs, or the `github` preset = GitHub `/meta` hook ranges,
    refreshed daily); the client IP honours `teams2.webhook_trusted_proxies`.
    Empty = off (HMAC stays the primary control)."""

    @model_validator(mode="after")
    def _checks(self) -> WebhookManifest:
        import ipaddress

        for cidr in self.allowed_cidrs:
            if cidr == "github":  # I6 preset (automation/github_meta.py)
                continue
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"allowed_cidrs: invalid CIDR {cidr!r}") from exc
        if self.script is not None and (self.script.startswith("/") or ".." in self.script.split("/")):
            raise ValueError("script must be a relative path under <home>/scripts/ without '..'")
        return self
