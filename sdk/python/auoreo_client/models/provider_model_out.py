from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_model_out_kind import ProviderModelOutKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ProviderModelOut")


@_attrs_define
class ProviderModelOut:
    """
    Attributes:
        kind (ProviderModelOutKind):
        litellm_id (str): What the gateway is called with: `<prefix><model_id>`
        model_id (str):
        name (str):
        capabilities (list[str] | Unset):
        context_window (int | None | Unset):
        input_price (float | None | Unset): USD per 1M input tokens
        output_price (float | None | Unset): USD per 1M output tokens
    """

    kind: ProviderModelOutKind
    litellm_id: str
    model_id: str
    name: str
    capabilities: list[str] | Unset = UNSET
    context_window: int | None | Unset = UNSET
    input_price: float | None | Unset = UNSET
    output_price: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        litellm_id = self.litellm_id

        model_id = self.model_id

        name = self.name

        capabilities: list[str] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = self.capabilities

        context_window: int | None | Unset
        if isinstance(self.context_window, Unset):
            context_window = UNSET
        else:
            context_window = self.context_window

        input_price: float | None | Unset
        if isinstance(self.input_price, Unset):
            input_price = UNSET
        else:
            input_price = self.input_price

        output_price: float | None | Unset
        if isinstance(self.output_price, Unset):
            output_price = UNSET
        else:
            output_price = self.output_price

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "litellm_id": litellm_id,
                "model_id": model_id,
                "name": name,
            }
        )
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if context_window is not UNSET:
            field_dict["context_window"] = context_window
        if input_price is not UNSET:
            field_dict["input_price"] = input_price
        if output_price is not UNSET:
            field_dict["output_price"] = output_price

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = ProviderModelOutKind(d.pop("kind"))

        litellm_id = d.pop("litellm_id")

        model_id = d.pop("model_id")

        name = d.pop("name")

        capabilities = cast(list[str], d.pop("capabilities", UNSET))

        def _parse_context_window(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        context_window = _parse_context_window(d.pop("context_window", UNSET))

        def _parse_input_price(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        input_price = _parse_input_price(d.pop("input_price", UNSET))

        def _parse_output_price(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        output_price = _parse_output_price(d.pop("output_price", UNSET))

        provider_model_out = cls(
            kind=kind,
            litellm_id=litellm_id,
            model_id=model_id,
            name=name,
            capabilities=capabilities,
            context_window=context_window,
            input_price=input_price,
            output_price=output_price,
        )

        provider_model_out.additional_properties = d
        return provider_model_out

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
