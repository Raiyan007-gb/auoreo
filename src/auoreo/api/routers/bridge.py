"""`POST /api/bridge/inbox` — mail from a peer instance (W2-B, spec P2).

Not behind the bearer token: the HMAC signature against `settings.bridges[<sender>]` IS the
authentication. Requests over the daemon's Unix socket are trusted by file permissions.
"""

from fastapi import APIRouter, HTTPException, Request

from auoreo.api.deps import ServicesDep
from auoreo.remote import bridge
from auoreo.remote.uds import is_unix_scope
from auoreo.schema.bridge import BridgeDelivered, BridgeEnvelope

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
_ERRORS = {
    code: {"description": text}
    for code, text in {
        401: "Bad signature, stale timestamp or replayed nonce",
        403: "Unknown bridge",
        404: "No such team or member here",
        503: "Teams are not running",
    }.items()
}


def _nonces(request: Request) -> bridge.NonceCache:
    state = request.app.state
    if getattr(state, "bridge_nonces", None) is None:
        state.bridge_nonces = bridge.NonceCache()
    return state.bridge_nonces  # type: ignore[no-any-return]


@router.post("/inbox", response_model=BridgeDelivered, responses=_ERRORS)  # type: ignore[arg-type]
async def inbox(envelope: BridgeEnvelope, request: Request, svc: ServicesDep) -> BridgeDelivered:
    """Verify `X-Auoreo-Signature` and deliver into the local team mailbox as
    `from: bridge:<sender>/<member>`. The signature covers the raw body bytes."""
    del envelope  # validated for the contract; the raw bytes are what was signed
    try:
        return await bridge.receive(
            svc,
            _nonces(request),
            body=await request.body(),
            headers=request.headers,
            over_uds=is_unix_scope(request.scope),
        )
    except bridge.BridgeRefused as exc:
        raise HTTPException(exc.status, exc.reason) from exc
