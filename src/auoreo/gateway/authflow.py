"""Credential recovery for the LiteLLM gateway (spec §25.9 L1009, §25.14 L1067). No `litellm`.

One `AuthFlow` per gateway. Per request attempt (`attempt()`), at most once each:

1. key pool (`provider/<name>/<slot>` secrets): 401 → the slot's OAuth refresh hook once,
   else 401/429 → `pool.rotate()` (failed slot cools down) and retry with the next slot;
2. store re-read: the deployment's cached credentials differ from the secret store (a key
   rotated while running) → refresh them and the Router, retry;
3. rotation grace: `SecretStore.candidates(name)` still holds `<name>#prev` → retry once with
   the previous key and audit `secret.rotated_fallback{name}` (names only, never values).

`maybe_reload()` notices writes from another process (CLI `secrets rotate`) by the
`secrets.db` mtime and hot-reloads the Router; the API calls `reload_credentials()` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from auoreo.gateway.errors import GatewayError
from auoreo.gateway.keypool import KeyPool, pools_from
from auoreo.gateway.pipeline import provider_of

if TYPE_CHECKING:
    from auoreo.gateway.gateway import LiteLLMGateway

log = logging.getLogger(__name__)
SLOT_KEY = "auoreo_key_slot"
"""`metadata` key carrying the key-pool slot NAME used by a request (never the value)."""
AuditHook = Callable[[str, dict[str, Any]], None]


class AuthFlow:
    def __init__(self, gw: LiteLLMGateway) -> None:
        self.gw = gw
        self.store: Any = None  # core.secrets.SecretStore, attached by the I2 starter
        self.audit_hook: AuditHook | None = None
        self.pools: dict[str, KeyPool] = {}
        self._stamp: int | None = None

    # ------------------------------------------------------------ attach / reload
    def attach(self, store: Any, audit_hook: AuditHook | None = None) -> None:
        self.store, self.audit_hook = store, audit_hook
        self._stamp = self._mtime()
        self.reload_pools()

    def _mtime(self) -> int | None:
        try:
            return int(self.store.path.stat().st_mtime_ns) if self.store is not None else None
        except OSError:
            return None

    def _names(self) -> list[str]:
        try:
            return list(self.store.names()) if self.store is not None else []
        except Exception:  # an unreadable store means "no pool", never a failed request
            return []

    def maybe_reload(self) -> bool:
        """Hot reload when `secrets.db` changed on disk since the last look."""
        if self.store is None or (stamp := self._mtime()) == self._stamp:
            return False
        self._stamp = stamp
        log.info("secret store changed on disk; reloading gateway credentials")
        self.gw.reload_credentials()
        return True

    def reload_pools(self) -> None:
        cfg = self.gw.settings.i2.keypool
        fresh = pools_from(self._names, self.gw.secret, cfg.strategy, cfg.cooldown_s)
        for prov, pool in fresh.items():
            if prov in self.pools:  # keep cooldown state of surviving slots
                self.pools[prov].reload()
                fresh[prov] = self.pools[prov]
            else:
                pool.reload()
        self.pools = fresh
        self._stamp = self._mtime()

    # ------------------------------------------------------------ pools
    def _pool_for(self, deployments: list[str]) -> KeyPool | None:
        from auoreo.gateway.deploy import provider_id

        provs = {provider_of(provider_id(self.gw.settings, d)) for d in deployments}
        return self.pools.get(provs.pop()) if len(provs) == 1 else None

    def pooled(self, model: str) -> bool:
        pool = self._pool_for([model])
        return pool is not None and pool.has_credentials()

    def apply_pool(self, kwargs: dict[str, Any], deployments: list[str]) -> None:
        """Pick a slot for this request (dynamic `api_key`; LiteLLM then skips its cached client)."""
        pool = self._pool_for(deployments)
        if pool is None or (sel := pool.select()) is None:
            return
        kwargs["api_key"] = sel[1]
        kwargs.setdefault("metadata", {})[SLOT_KEY] = sel[0]

    def succeeded(self, slot: str) -> None:
        for pool in self.pools.values():
            pool.succeeded(slot)

    def audit(self, action: str, detail: dict[str, Any]) -> None:
        if self.audit_hook is None:
            return
        try:
            self.audit_hook(action, detail)
        except Exception:  # auditing never breaks the request path
            log.exception("gateway audit hook failed")

    # ------------------------------------------------------------ store / #prev
    def recover_store(self, model: str) -> bool:
        """Re-read `model`'s deployment credentials; True (and a fresh Router) when changed."""
        changed = False
        for entry in self.gw._model_list:
            if entry["model_name"] != model:
                continue
            params = entry["litellm_params"]
            fresh = self.gw._creds(params["model"])
            if fresh and fresh != {k: params.get(k) for k in fresh}:
                params.update(fresh)
                changed = True
        if changed:
            log.warning("credentials for %s changed in the secret store; retrying once", model)
            self.gw._router = self.gw._make_router()
        return changed

    def prev_key(self, model: str) -> tuple[str, str] | None:
        """`(name, previous value)` while `<name>#prev` is inside its rotation grace window."""
        deployments = self.gw._deployments(model)
        if self.store is None or len({provider_of(d) for d in deployments}) != 1:
            return None  # a dynamic key must not reach another provider's deployment
        for name in self.gw.key_names(model):
            try:
                cands = self.store.candidates(name)
            except Exception:
                continue
            if len(cands) > 1:
                return name, cands[1]
        return None

    def attempt(self) -> Attempt:
        return Attempt(self)


class Attempt:
    """Recovery state of ONE request (each step at most once)."""

    def __init__(self, flow: AuthFlow) -> None:
        self.flow = flow
        self.done: set[str] = set()

    def recover(self, err: GatewayError, model: str, kwargs: dict[str, Any]) -> bool:
        """True when `kwargs` were updated and the call is worth retrying."""
        steps = (self._pool, self._store, self._prev) if err.code == "AUTH" else (self._pool,)
        if err.code not in {"AUTH", "RATE_LIMIT"}:
            return False
        return any(step(err, model, kwargs) for step in steps)

    def _once(self, name: str) -> bool:
        if name in self.done:
            return False
        self.done.add(name)
        return True

    def _pool(self, err: GatewayError, model: str, kwargs: dict[str, Any]) -> bool:
        meta = kwargs.get("metadata") or {}
        slot = meta.get(SLOT_KEY)
        pool = self.flow._pool_for(self.flow.gw._deployments(model))
        if not slot or pool is None or not self._once("pool"):
            return False
        if err.code == "AUTH" and (fresh := pool.refresh_once(slot)):
            kwargs["api_key"] = fresh
            self.flow.audit("gateway.key_refreshed", {"slot": slot})
            return True
        nxt = pool.rotate(slot, err.status)
        if nxt is None:
            return False
        kwargs["api_key"], meta[SLOT_KEY] = nxt[1], nxt[0]
        self.flow.audit("gateway.key_rotated", {"from": slot, "to": nxt[0], "code": err.code})
        return True

    def _store(self, err: GatewayError, model: str, kwargs: dict[str, Any]) -> bool:
        return self._once("store") and self.flow.recover_store(model)

    def _prev(self, err: GatewayError, model: str, kwargs: dict[str, Any]) -> bool:
        if (found := self.flow.prev_key(model)) is None or not self._once("prev"):
            return False
        name, value = found
        kwargs["api_key"] = value
        log.warning("%s rejected the current %s; trying the previous key (rotation grace)", model, name)
        self.flow.audit("secret.rotated_fallback", {"name": name, "model": model})
        return True


__all__ = ["SLOT_KEY", "Attempt", "AuditHook", "AuthFlow"]
