from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.webhook_route_info_provider import WebhookRouteInfoProvider

if TYPE_CHECKING:
    from ..models.webhook_route_info_filters_item import WebhookRouteInfoFiltersItem


T = TypeVar("T", bound="WebhookRouteInfo")


@_attrs_define
class WebhookRouteInfo:
    """
    Attributes:
        agent (str):
        deliver (list[str]):
        description (str):
        enabled (bool):
        events (list[str]):
        filters (list[WebhookRouteInfoFiltersItem]):
        max_body_bytes (int):
        name (str):
        path (str):
        provider (WebhookRouteInfoProvider):
        rate_limit_per_minute (int):
        secret_configured (bool): Whether the referenced secret resolves (value never shown)
        workflow (None | str):
    """

    agent: str
    deliver: list[str]
    description: str
    enabled: bool
    events: list[str]
    filters: list[WebhookRouteInfoFiltersItem]
    max_body_bytes: int
    name: str
    path: str
    provider: WebhookRouteInfoProvider
    rate_limit_per_minute: int
    secret_configured: bool
    workflow: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        deliver = self.deliver

        description = self.description

        enabled = self.enabled

        events = self.events

        filters = []
        for filters_item_data in self.filters:
            filters_item = filters_item_data.to_dict()
            filters.append(filters_item)

        max_body_bytes = self.max_body_bytes

        name = self.name

        path = self.path

        provider = self.provider.value

        rate_limit_per_minute = self.rate_limit_per_minute

        secret_configured = self.secret_configured

        workflow: None | str
        workflow = self.workflow

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "deliver": deliver,
                "description": description,
                "enabled": enabled,
                "events": events,
                "filters": filters,
                "max_body_bytes": max_body_bytes,
                "name": name,
                "path": path,
                "provider": provider,
                "rate_limit_per_minute": rate_limit_per_minute,
                "secret_configured": secret_configured,
                "workflow": workflow,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.webhook_route_info_filters_item import WebhookRouteInfoFiltersItem  # noqa: PLC0415

        d = dict(src_dict)
        agent = d.pop("agent")

        deliver = cast(list[str], d.pop("deliver"))

        description = d.pop("description")

        enabled = d.pop("enabled")

        events = cast(list[str], d.pop("events"))

        filters = []
        _filters = d.pop("filters")
        for filters_item_data in _filters:
            filters_item = WebhookRouteInfoFiltersItem.from_dict(filters_item_data)

            filters.append(filters_item)

        max_body_bytes = d.pop("max_body_bytes")

        name = d.pop("name")

        path = d.pop("path")

        provider = WebhookRouteInfoProvider(d.pop("provider"))

        rate_limit_per_minute = d.pop("rate_limit_per_minute")

        secret_configured = d.pop("secret_configured")

        def _parse_workflow(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        workflow = _parse_workflow(d.pop("workflow"))

        webhook_route_info = cls(
            agent=agent,
            deliver=deliver,
            description=description,
            enabled=enabled,
            events=events,
            filters=filters,
            max_body_bytes=max_body_bytes,
            name=name,
            path=path,
            provider=provider,
            rate_limit_per_minute=rate_limit_per_minute,
            secret_configured=secret_configured,
            workflow=workflow,
        )

        webhook_route_info.additional_properties = d
        return webhook_route_info

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
