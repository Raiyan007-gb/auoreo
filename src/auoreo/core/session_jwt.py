"""Short-lived browser session tokens and per-session `/ws` capabilities (spec §18, §24.7, §25.14).

`POST /api/auth/login` trades the server password for an HS256 JWT (`exp` ≤ 15 min, `jti`,
`sid`, optionally bound to the client IP) that lives in the HttpOnly cookie, so the cookie is
never the raw password. Activity refreshes it (same `sid`, new `jti`); logout revokes the
`sid` (every token of that login) until its last possible expiry. Capability tokens
(`typ=cap`) carry one `session_id` and authorize only `/ws` for that session.

The signing key is a secret: env / keyring / `secrets.db` under `auth/jwt-signing-key`,
generated on first use. When no store can hold it (e.g. `forbid-file` with no keyring) a
process-local key is used and every token dies with the process. Tokens are never logged.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import jwt

log = logging.getLogger(__name__)
KEY_SECRET = "auth/jwt-signing-key"
ALGORITHM = "HS256"
ISSUER = "auoreo"
MAX_TTL_S = 900
Kind = Literal["session", "cap"]


@dataclass(frozen=True)
class Claims:
    sid: str
    jti: str
    iat: int
    exp: int
    typ: Kind
    ip: str | None = None
    session_id: str | None = None

    @property
    def age_s(self) -> float:
        return time.time() - self.iat


def looks_like_jwt(value: str | None) -> bool:
    return bool(value) and value.count(".") == 2 and value.startswith("eyJ")  # type: ignore[union-attr]


class RevocationList:
    """`sid`/`jti` → expiry. In memory, mirrored to `<home>/auth/revoked.json` (0600) so a
    restart does not resurrect a logged-out session; entries drop once they expire."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[str, int] = self._read()

    def _read(self) -> dict[str, int]:
        if self.path is None:
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = int(time.time())
        return {str(k): int(v) for k, v in raw.items() if isinstance(v, int) and v > now}

    def _write(self) -> None:
        if self.path is None:
            return
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._items, fh)
            tmp.replace(self.path)

    def add(self, key: str, until: int) -> None:
        with self._lock:
            now = int(time.time())
            self._items = {k: v for k, v in self._items.items() if v > now}
            self._items[key] = until
            self._write()

    def __contains__(self, key: str) -> bool:
        until = self._items.get(key)
        return until is not None and until > time.time()

    def __len__(self) -> int:
        now = time.time()
        return sum(1 for v in self._items.values() if v > now)


class SessionTokens:
    def __init__(self, settings: Any, store: Any | None) -> None:
        self.settings = settings
        self.store = store
        self._key: bytes | None = None
        self.ephemeral = False
        home = getattr(settings, "home", None)
        self.revoked = RevocationList(Path(home) / "auth" / "revoked.json" if home else None)

    # ------------------------------------------------------------------ key
    def key(self) -> bytes:
        if self._key is not None:
            return self._key
        value: str | None = None
        if self.store is not None:
            try:
                value = self.store.get(KEY_SECRET)
                if not value:
                    value = secrets.token_urlsafe(48)
                    self.store.set(KEY_SECRET, value)
            except Exception as exc:  # no writable store: tokens die with this process
                log.warning("session JWT key not persisted (%s); tokens end with this process", exc)
                value = None
        if not value:
            value, self.ephemeral = secrets.token_urlsafe(48), True
        self._key = value.encode()
        return self._key

    @property
    def _cfg(self) -> Any:
        return self.settings.h1b

    # ------------------------------------------------------------------ mint
    def mint(
        self,
        *,
        ip: str | None,
        sid: str | None = None,
        kind: Kind = "session",
        session_id: str | None = None,
    ) -> tuple[str, Claims]:
        ttl = self._cfg.capability_ttl_s if kind == "cap" else min(self._cfg.jwt_ttl_s, MAX_TTL_S)
        now = int(time.time())
        claims = Claims(
            sid=sid or uuid.uuid4().hex,
            jti=uuid.uuid4().hex,
            iat=now,
            exp=now + ttl,
            typ=kind,
            ip=ip if self._cfg.jwt_bind_ip else None,
            session_id=session_id,
        )
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "sub": "browser" if kind == "session" else f"session:{session_id}",
            "sid": claims.sid,
            "jti": claims.jti,
            "iat": claims.iat,
            "exp": claims.exp,
            "typ": kind,
        }
        if claims.ip:
            payload["ip"] = claims.ip
        if session_id:
            payload["session_id"] = session_id
        return jwt.encode(payload, self.key(), algorithm=ALGORITHM), claims

    def refresh(self, old: Claims, ip: str | None) -> tuple[str, Claims]:
        """Same login (`sid`), new `jti` + expiry."""
        return self.mint(ip=ip, sid=old.sid, kind=old.typ, session_id=old.session_id)

    # ------------------------------------------------------------------ verify
    def verify(self, token: str | None, *, ip: str | None = None, kind: Kind = "session") -> Claims | None:
        """Claims for a valid, unrevoked token of `kind` (bound IP must match), else None."""
        if not token or not looks_like_jwt(token):
            return None
        try:
            data = jwt.decode(
                token,
                self.key(),
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["exp", "iat", "jti", "sid", "typ"]},
            )
        except jwt.InvalidTokenError:
            return None
        if data.get("typ") != kind or int(data["exp"]) - int(data["iat"]) > max(MAX_TTL_S, 3600):
            return None
        claims = Claims(
            sid=str(data["sid"]),
            jti=str(data["jti"]),
            iat=int(data["iat"]),
            exp=int(data["exp"]),
            typ=kind,
            ip=data.get("ip"),
            session_id=data.get("session_id"),
        )
        if claims.sid in self.revoked or claims.jti in self.revoked:
            return None
        if claims.ip and ip and claims.ip != ip:
            return None
        return claims

    def is_live(self, claims: Claims) -> bool:
        return claims.exp > time.time() and claims.sid not in self.revoked and claims.jti not in self.revoked

    def needs_refresh(self, claims: Claims) -> bool:
        return claims.typ == "session" and claims.age_s >= self._cfg.jwt_refresh_after_s

    def revoke(self, claims: Claims) -> None:
        """Logout: the whole login (`sid`) until the latest expiry any refresh could reach."""
        until = int(time.time()) + max(self._cfg.jwt_ttl_s, self._cfg.capability_ttl_s) + 60
        self.revoked.add(claims.sid, until)
        self.revoked.add(claims.jti, claims.exp + 60)


def tokens_for(services: Any) -> SessionTokens:
    """The per-`Services` token minter (kept in `services.ext`)."""
    ext = getattr(services, "ext", None)
    if not isinstance(ext, dict):
        return SessionTokens(services.settings, getattr(services, "secrets", None))
    found = ext.get("session_tokens")
    if not isinstance(found, SessionTokens) or found.settings is not services.settings:
        found = ext["session_tokens"] = SessionTokens(services.settings, getattr(services, "secrets", None))
    return found
