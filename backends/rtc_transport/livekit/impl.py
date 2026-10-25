"""LiveKit RTC transport (spec §26.8; P2, opt-in): rooms on an SFU, SIP telephony, egress.

`livekit-api` (extra `livekit`) is imported lazily, so nothing below P2 depends on it.
- `join` creates room `auoreo-<session_id>` (`RoomService.CreateRoom`) and mints a token
  scoped to that room (`room_join`, identity, TTL `settings.livekit.token_ttl_s` = 1 h).
- SIP: `create_sip_inbound` refuses `numbers=[]` without digest auth or allowed addresses
  and always sets `media_encryption = SIP_MEDIA_ENCRYPT_REQUIRE` (SRTP); the outbound trunk
  uses TLS transport. No REGISTER, no video. `transfer` is a cold SIP REFER.
- Egress: only `StartEgress` with a `TemplateSource{audio_only: true}`.
- `subscribable()` drops EGRESS / INGRESS / AGENT participants from any subscription logic.
- Self-hosted by default: a `*.livekit.cloud` URL fails unless `cloud_consent` is true.
Tokens and API secrets never reach logs, events or `describe()`.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.rtc_transport import JoinInfo, RtcTransport
from auoreo.core.secrets import SecretStore

INSTALL_HINT = "install auoreo[livekit]"
SKIP_KINDS = frozenset({"EGRESS", "INGRESS", "AGENT"})
CONSENT_MSG = (
    "settings.livekit.url points at LiveKit Cloud; media and metadata would leave your network. "
    "Set settings.livekit.cloud_consent = true to allow it, or self-host livekit-server"
)


class LiveKitUnavailable(RuntimeError):
    """`livekit-api` is not installed or the transport is not configured."""


class ConsentError(PermissionError):
    """A LiveKit Cloud URL without `cloud_consent`."""


def sdk() -> Any:
    try:
        from livekit import api
    except ImportError as exc:
        raise LiveKitUnavailable(f"livekit-api is not installed; {INSTALL_HINT}") from exc
    return api


def is_cloud(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "livekit.cloud" or host.endswith(".livekit.cloud")


def http_url(url: str) -> str:
    """Server API base: `wss://h` → `https://h`, `ws://h` → `http://h`."""
    return re.sub(r"^ws(s?)://", r"http\1://", url.rstrip("/"))


def room_for(session_id: str | None) -> str:
    sid = re.sub(r"[^A-Za-z0-9_-]", "-", session_id or uuid.uuid4().hex[:12])
    return f"auoreo-{sid}"[:128]


def kind_name(participant: Any) -> str:
    kind = participant.get("kind") if isinstance(participant, dict) else getattr(participant, "kind", 0)
    if isinstance(kind, str):
        return kind.upper()
    try:
        from livekit.protocol import models

        return str(models.ParticipantInfo.Kind.Name(int(kind or 0)))
    except (ImportError, ValueError):
        return "STANDARD"


def subscribable(participants: Iterable[Any]) -> list[Any]:
    """Participants worth subscribing to: never egress/ingress recorders or other agents."""
    return [p for p in participants if kind_name(p) not in SKIP_KINDS]


class Backend(RtcTransport):
    name = "livekit"

    def __init__(self, settings: Any, *, api_factory: Callable[[], Any] | None = None) -> None:
        super().__init__(settings)
        self.api_factory = api_factory
        """Test seam: returns a `LiveKitAPI`-shaped object (room/sip/egress + aclose)."""
        self.rooms: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- config
    @property
    def cfg(self) -> Any:
        return self.settings.livekit

    def _secret(self, name: str) -> str | None:
        store = SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file"))
        return store.get(name)

    def _keys(self) -> tuple[str, str]:
        key, secret = self._secret(self.cfg.api_key_secret), self._secret(self.cfg.api_secret_secret)
        if not key or not secret:
            raise LiveKitUnavailable(
                f"{self.cfg.api_key_secret}/{self.cfg.api_secret_secret} are not set; "
                "run `auoreo secrets set` for both"
            )
        return key, secret

    def _url(self) -> str:
        url = self.cfg.url
        if not url:
            raise LiveKitUnavailable("settings.livekit.url is not set")
        if is_cloud(url) and not self.cfg.cloud_consent:
            raise ConsentError(CONSENT_MSG)
        return str(url)

    # -------------------------------------------------------------- check
    async def check(self) -> BackendStatus:
        url = self.cfg.url
        if not url:
            return BackendStatus(False, "settings.livekit.url is not set", "set settings.livekit.url")
        if is_cloud(url) and not self.cfg.cloud_consent:
            return BackendStatus(False, CONSENT_MSG, "set settings.livekit.cloud_consent or self-host")
        missing = [n for n in (self.cfg.api_key_secret, self.cfg.api_secret_secret) if not self._secret(n)]
        if missing:
            return BackendStatus(False, f"missing secrets {missing}", f"auoreo secrets set {missing[0]}")
        try:
            sdk()
        except LiveKitUnavailable as exc:
            return BackendStatus(False, str(exc), INSTALL_HINT)
        where = "LiveKit Cloud (consented)" if is_cloud(url) else "self-hosted"
        if os.environ.get("AUOREO_OFFLINE"):
            return BackendStatus(True, f"{where} {url}; reachability skipped (AUOREO_OFFLINE)")
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(http_url(url) + "/")
        except httpx.HTTPError as exc:
            return BackendStatus(False, f"{where} {url} unreachable ({type(exc).__name__})", "is it running?")
        return BackendStatus(True, f"{where} {url} reachable (HTTP {resp.status_code})")

    # --------------------------------------------------------------- API
    def _api(self) -> Any:
        if self.api_factory is not None:
            return self.api_factory()
        url = self._url()
        key, secret = self._keys()
        return sdk().LiveKitAPI(http_url(url), key, secret)

    async def _call(self, fn: Callable[[Any], Any]) -> Any:
        self._url()  # consent gate first, even with a test factory
        lk = self._api()
        try:
            return await fn(lk)
        except (LiveKitUnavailable, PermissionError, ValueError):
            raise
        except Exception as exc:  # aiohttp / Twirp errors: never echo headers or tokens
            raise ConnectionError(f"LiveKit API call failed: {type(exc).__name__}: {exc}"[:300]) from None
        finally:
            closer = getattr(lk, "aclose", None)
            if closer is not None:
                await closer()

    def mint_token(self, identity: str, room: str, ttl_s: int | None = None) -> str:
        api = sdk()
        key, secret = self._keys()
        ttl = timedelta(seconds=ttl_s or int(self.cfg.token_ttl_s))
        grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
        token = api.AccessToken(key, secret).with_identity(identity).with_ttl(ttl).with_grants(grants)
        return str(token.to_jwt())

    # ------------------------------------------------------------ transport
    async def join(self, *, session_id: str | None, identity: str, offer_sdp: str | None) -> JoinInfo:
        url = self._url()
        room = room_for(session_id)
        api = sdk()
        req = api.CreateRoomRequest(name=room, empty_timeout=300, max_participants=16)
        await self._call(lambda lk: lk.room.create_room(req))
        token = self.mint_token(identity, room)
        ttl = int(self.cfg.token_ttl_s)
        self.rooms[room] = {
            "join_id": room,
            "room": room,
            "session_id": session_id,
            "identity": identity,
            "created_at": datetime.now(UTC),
        }
        extra = {"join_id": room, "ttl_s": ttl, "cloud": is_cloud(url)}
        return JoinInfo("livekit", session_id, url=url, token=token, room=room, extra=extra)

    async def leave(self, join_id: str) -> None:
        if self.rooms.pop(join_id, None) is None:
            return
        api = sdk()
        await self._call(lambda lk: lk.room.delete_room(api.DeleteRoomRequest(room=join_id)))

    async def describe(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rooms.values()]

    # ---------------------------------------------------------------- SIP
    def inbound_trunk_request(
        self,
        numbers: Sequence[str],
        *,
        allowed_addresses: Sequence[str] = (),
        auth_username: str | None = None,
        auth_password: str | None = None,
        name: str = "auoreo-inbound",
    ) -> Any:
        """The `CreateSIPInboundTrunkRequest`, validated (no network)."""
        has_auth = bool(auth_username and auth_password)
        if not numbers and not allowed_addresses and not has_auth:
            raise ValueError(
                "an inbound trunk with numbers=[] accepts calls for any number: "
                "set allowed_addresses or auth_username/auth_password"
            )
        api = sdk()
        from livekit.protocol import sip

        trunk = sip.SIPInboundTrunkInfo(
            name=name,
            numbers=list(numbers),
            allowed_addresses=list(allowed_addresses),
            auth_username=auth_username or "",
            auth_password=auth_password or "",
            krisp_enabled=bool(self.cfg.sip.krisp_enabled),
            media_encryption=sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
        )
        return api.CreateSIPInboundTrunkRequest(trunk=trunk)

    async def create_sip_inbound(
        self,
        numbers: Sequence[str],
        *,
        allowed_addresses: Sequence[str] = (),
        auth_username: str | None = None,
        auth_password: str | None = None,
    ) -> str:
        """Returns the new trunk id."""
        req = self.inbound_trunk_request(
            numbers,
            allowed_addresses=allowed_addresses,
            auth_username=auth_username,
            auth_password=auth_password,
        )
        info = await self._call(lambda lk: lk.sip.create_sip_inbound_trunk(req))
        return str(getattr(info, "sip_trunk_id", ""))

    async def create_sip_outbound(
        self, address: str, numbers: Sequence[str], *, auth_username: str = "", auth_password: str = ""
    ) -> str:
        """Outbound trunk over TLS with SRTP required (no REGISTER: the carrier authenticates us)."""
        if not numbers:
            raise ValueError("an outbound trunk needs at least one caller number")
        api = sdk()
        from livekit.protocol import sip

        trunk = sip.SIPOutboundTrunkInfo(
            name="auoreo-outbound",
            address=address,
            numbers=list(numbers),
            transport=sip.SIPTransport.SIP_TRANSPORT_TLS,
            auth_username=auth_username,
            auth_password=auth_password,
            media_encryption=sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
        )
        req = api.CreateSIPOutboundTrunkRequest(trunk=trunk)
        info = await self._call(lambda lk: lk.sip.create_sip_outbound_trunk(req))
        return str(getattr(info, "sip_trunk_id", ""))

    def dispatch_rule_request(self, room_prefix: str | None = None, trunk_ids: Sequence[str] = ()) -> Any:
        api = sdk()
        from livekit.protocol import sip

        prefix = room_prefix or str(self.cfg.sip.room_prefix)
        rule = sip.SIPDispatchRule(dispatch_rule_individual=sip.SIPDispatchRuleIndividual(room_prefix=prefix))
        return api.CreateSIPDispatchRuleRequest(rule=rule, trunk_ids=list(trunk_ids), name="auoreo-dispatch")

    async def create_dispatch_rule(
        self, room_prefix: str | None = None, trunk_ids: Sequence[str] = ()
    ) -> str:
        """One room per caller (`<prefix><random>`); returns the rule id."""
        req = self.dispatch_rule_request(room_prefix, trunk_ids)
        info = await self._call(lambda lk: lk.sip.create_sip_dispatch_rule(req))
        return str(getattr(info, "sip_dispatch_rule_id", ""))

    async def transfer(self, room: str, participant_identity: str, transfer_to: str) -> None:
        """Cold transfer (SIP REFER) of a phone participant to `tel:` / `sip:` `transfer_to`."""
        if not transfer_to.startswith(("tel:", "sip:")):
            raise ValueError("transfer_to must be a tel: or sip: URI")
        from livekit.protocol import sip

        req = sip.TransferSIPParticipantRequest(
            room_name=room, participant_identity=participant_identity, transfer_to=transfer_to
        )
        await self._call(lambda lk: lk.sip.transfer_sip_participant(req))

    # ------------------------------------------------------------- egress
    def audio_egress_request(self, room: str, filepath: str | None = None) -> Any:
        """`StartEgress{TemplateSource{audio_only}}` → one OGG file (the only egress shape used)."""
        from livekit.protocol import egress

        out = egress.Output(
            file=egress.FileOutput(
                file_type=egress.EncodedFileType.OGG, filepath=filepath or f"{room}/{{time}}.ogg"
            )
        )
        return egress.StartEgressRequest(
            room_name=room, template=egress.TemplateSource(audio_only=True), outputs=[out]
        )

    async def start_audio_egress(self, room: str, filepath: str | None = None) -> str:
        req = self.audio_egress_request(room, filepath)
        info = await self._call(lambda lk: lk.egress.start_egress(req))
        return str(getattr(info, "egress_id", ""))
