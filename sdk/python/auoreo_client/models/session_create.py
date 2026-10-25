from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionCreate")


@_attrs_define
class SessionCreate:
    """
    Attributes:
        agent (None | str | Unset):
        model (None | str | Unset):
        title (None | str | Unset):
        workflow (None | str | Unset):
    """

    agent: None | str | Unset = UNSET
    model: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    workflow: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agent: None | str | Unset
        if isinstance(self.agent, Unset):
            agent = UNSET
        else:
            agent = self.agent

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        workflow: None | str | Unset
        if isinstance(self.workflow, Unset):
            workflow = UNSET
        else:
            workflow = self.workflow

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agent is not UNSET:
            field_dict["agent"] = agent
        if model is not UNSET:
            field_dict["model"] = model
        if title is not UNSET:
            field_dict["title"] = title
        if workflow is not UNSET:
            field_dict["workflow"] = workflow

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent = _parse_agent(d.pop("agent", UNSET))

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_workflow(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        workflow = _parse_workflow(d.pop("workflow", UNSET))

        session_create = cls(
            agent=agent,
            model=model,
            title=title,
            workflow=workflow,
        )

        return session_create
