from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.chat_request_mode import ChatRequestMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChatRequest")


@_attrs_define
class ChatRequest:
    """
    Attributes:
        message (str):
        agent (None | str | Unset):
        mode (ChatRequestMode | Unset):  Default: ChatRequestMode.FOLLOWUP.
        model (None | str | Unset):
        request_id (None | str | Unset): Idempotency key (UUID)
        session_id (None | str | Unset):
        workflow (None | str | Unset):
    """

    message: str
    agent: None | str | Unset = UNSET
    mode: ChatRequestMode | Unset = ChatRequestMode.FOLLOWUP
    model: None | str | Unset = UNSET
    request_id: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET
    workflow: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        message = self.message

        agent: None | str | Unset
        if isinstance(self.agent, Unset):
            agent = UNSET
        else:
            agent = self.agent

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        workflow: None | str | Unset
        if isinstance(self.workflow, Unset):
            workflow = UNSET
        else:
            workflow = self.workflow

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "message": message,
            }
        )
        if agent is not UNSET:
            field_dict["agent"] = agent
        if mode is not UNSET:
            field_dict["mode"] = mode
        if model is not UNSET:
            field_dict["model"] = model
        if request_id is not UNSET:
            field_dict["request_id"] = request_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if workflow is not UNSET:
            field_dict["workflow"] = workflow

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        message = d.pop("message")

        def _parse_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent = _parse_agent(d.pop("agent", UNSET))

        _mode = d.pop("mode", UNSET)
        mode: ChatRequestMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = ChatRequestMode(_mode)

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_workflow(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        workflow = _parse_workflow(d.pop("workflow", UNSET))

        chat_request = cls(
            message=message,
            agent=agent,
            mode=mode,
            model=model,
            request_id=request_id,
            session_id=session_id,
            workflow=workflow,
        )

        return chat_request
