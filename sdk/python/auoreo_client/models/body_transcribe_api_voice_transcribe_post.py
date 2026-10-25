from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyTranscribeApiVoiceTranscribePost")


@_attrs_define
class BodyTranscribeApiVoiceTranscribePost:
    """
    Attributes:
        file (str): Audio (wav, webm, ogg, mp3, m4a, …)
        language (None | str | Unset):
        session_id (None | str | Unset):
    """

    file: str
    language: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file = self.file

        language: None | str | Unset
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file": file,
            }
        )
        if language is not UNSET:
            field_dict["language"] = language
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", (None, str(self.file).encode(), "text/plain")))

        if not isinstance(self.language, Unset):
            if isinstance(self.language, str):
                files.append(("language", (None, str(self.language).encode(), "text/plain")))
            else:
                files.append(("language", (None, str(self.language).encode(), "text/plain")))

        if not isinstance(self.session_id, Unset):
            if isinstance(self.session_id, str):
                files.append(("session_id", (None, str(self.session_id).encode(), "text/plain")))
            else:
                files.append(("session_id", (None, str(self.session_id).encode(), "text/plain")))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        file = d.pop("file")

        def _parse_language(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        body_transcribe_api_voice_transcribe_post = cls(
            file=file,
            language=language,
            session_id=session_id,
        )

        body_transcribe_api_voice_transcribe_post.additional_properties = d
        return body_transcribe_api_voice_transcribe_post

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
