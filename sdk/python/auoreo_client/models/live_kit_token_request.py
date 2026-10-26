from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.live_kit_room_config import LiveKitRoomConfig
    from ..models.live_kit_token_request_participant_attributes_type_0 import (
        LiveKitTokenRequestParticipantAttributesType0,
    )


T = TypeVar("T", bound="LiveKitTokenRequest")


@_attrs_define
class LiveKitTokenRequest:
    """
    Attributes:
        participant_attributes (LiveKitTokenRequestParticipantAttributesType0 | None | Unset): Token attributes (size-
            capped)
        participant_identity (None | str | Unset): Ignored: the server mints an opaque uuid4 identity (spec §26.10)
        participant_metadata (None | str | Unset): Token metadata (size-capped)
        participant_name (None | str | Unset):
        room_config (LiveKitRoomConfig | None | Unset):
        room_name (None | str | Unset): An existing room from this server (default: the session's room)
        session_id (None | str | Unset): AUOREO session the room belongs to
    """

    participant_attributes: LiveKitTokenRequestParticipantAttributesType0 | None | Unset = UNSET
    participant_identity: None | str | Unset = UNSET
    participant_metadata: None | str | Unset = UNSET
    participant_name: None | str | Unset = UNSET
    room_config: LiveKitRoomConfig | None | Unset = UNSET
    room_name: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.live_kit_room_config import LiveKitRoomConfig  # noqa: PLC0415
        from ..models.live_kit_token_request_participant_attributes_type_0 import (
            LiveKitTokenRequestParticipantAttributesType0,  # noqa: PLC0415
        )

        participant_attributes: dict[str, Any] | None | Unset
        if isinstance(self.participant_attributes, Unset):
            participant_attributes = UNSET
        elif isinstance(self.participant_attributes, LiveKitTokenRequestParticipantAttributesType0):
            participant_attributes = self.participant_attributes.to_dict()
        else:
            participant_attributes = self.participant_attributes

        participant_identity: None | str | Unset
        if isinstance(self.participant_identity, Unset):
            participant_identity = UNSET
        else:
            participant_identity = self.participant_identity

        participant_metadata: None | str | Unset
        if isinstance(self.participant_metadata, Unset):
            participant_metadata = UNSET
        else:
            participant_metadata = self.participant_metadata

        participant_name: None | str | Unset
        if isinstance(self.participant_name, Unset):
            participant_name = UNSET
        else:
            participant_name = self.participant_name

        room_config: dict[str, Any] | None | Unset
        if isinstance(self.room_config, Unset):
            room_config = UNSET
        elif isinstance(self.room_config, LiveKitRoomConfig):
            room_config = self.room_config.to_dict()
        else:
            room_config = self.room_config

        room_name: None | str | Unset
        if isinstance(self.room_name, Unset):
            room_name = UNSET
        else:
            room_name = self.room_name

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if participant_attributes is not UNSET:
            field_dict["participant_attributes"] = participant_attributes
        if participant_identity is not UNSET:
            field_dict["participant_identity"] = participant_identity
        if participant_metadata is not UNSET:
            field_dict["participant_metadata"] = participant_metadata
        if participant_name is not UNSET:
            field_dict["participant_name"] = participant_name
        if room_config is not UNSET:
            field_dict["room_config"] = room_config
        if room_name is not UNSET:
            field_dict["room_name"] = room_name
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.live_kit_room_config import LiveKitRoomConfig  # noqa: PLC0415
        from ..models.live_kit_token_request_participant_attributes_type_0 import (
            LiveKitTokenRequestParticipantAttributesType0,  # noqa: PLC0415
        )

        d = dict(src_dict)

        def _parse_participant_attributes(
            data: object,
        ) -> LiveKitTokenRequestParticipantAttributesType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                participant_attributes_type_0 = LiveKitTokenRequestParticipantAttributesType0.from_dict(data)

                return participant_attributes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(LiveKitTokenRequestParticipantAttributesType0 | None | Unset, data)

        participant_attributes = _parse_participant_attributes(d.pop("participant_attributes", UNSET))

        def _parse_participant_identity(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        participant_identity = _parse_participant_identity(d.pop("participant_identity", UNSET))

        def _parse_participant_metadata(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        participant_metadata = _parse_participant_metadata(d.pop("participant_metadata", UNSET))

        def _parse_participant_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        participant_name = _parse_participant_name(d.pop("participant_name", UNSET))

        def _parse_room_config(data: object) -> LiveKitRoomConfig | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                room_config_type_0 = LiveKitRoomConfig.from_dict(data)

                return room_config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(LiveKitRoomConfig | None | Unset, data)

        room_config = _parse_room_config(d.pop("room_config", UNSET))

        def _parse_room_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        room_name = _parse_room_name(d.pop("room_name", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        live_kit_token_request = cls(
            participant_attributes=participant_attributes,
            participant_identity=participant_identity,
            participant_metadata=participant_metadata,
            participant_name=participant_name,
            room_config=room_config,
            room_name=room_name,
            session_id=session_id,
        )

        return live_kit_token_request
