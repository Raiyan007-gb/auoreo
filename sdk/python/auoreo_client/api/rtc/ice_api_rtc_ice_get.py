from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.rtc_ice_config import RtcIceConfig
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    cookies = {}
    if auoreo_token is not UNSET:
        cookies["auoreo_token"] = auoreo_token

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/rtc/ice",
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | RtcIceConfig | None:
    if response.status_code == 200:
        response_200 = RtcIceConfig.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | RtcIceConfig]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | RtcIceConfig]:
    """Ice

     ICE servers for a browser `RTCPeerConnection` (F5): `rtc_ice_servers` plus, when
    `turn_secret_ref` + `turn_urls` are set, TURN REST credentials valid `turn_ttl_s` seconds.
    The shared secret is never returned; fetch this right before each offer.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RtcIceConfig]
    """

    kwargs = _get_kwargs(
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | RtcIceConfig | None:
    """Ice

     ICE servers for a browser `RTCPeerConnection` (F5): `rtc_ice_servers` plus, when
    `turn_secret_ref` + `turn_urls` are set, TURN REST credentials valid `turn_ttl_s` seconds.
    The shared secret is never returned; fetch this right before each offer.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RtcIceConfig
    """

    return sync_detailed(
        client=client,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | RtcIceConfig]:
    """Ice

     ICE servers for a browser `RTCPeerConnection` (F5): `rtc_ice_servers` plus, when
    `turn_secret_ref` + `turn_urls` are set, TURN REST credentials valid `turn_ttl_s` seconds.
    The shared secret is never returned; fetch this right before each offer.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RtcIceConfig]
    """

    kwargs = _get_kwargs(
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | RtcIceConfig | None:
    """Ice

     ICE servers for a browser `RTCPeerConnection` (F5): `rtc_ice_servers` plus, when
    `turn_secret_ref` + `turn_urls` are set, TURN REST credentials valid `turn_ttl_s` seconds.
    The shared secret is never returned; fetch this right before each offer.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RtcIceConfig
    """

    return (
        await asyncio_detailed(
            client=client,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
