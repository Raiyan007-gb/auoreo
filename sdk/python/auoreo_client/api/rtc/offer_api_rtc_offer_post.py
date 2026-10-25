from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.rtc_answer import RtcAnswer
from ...models.rtc_offer import RtcOffer
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: RtcOffer,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    cookies = {}
    if auoreo_token is not UNSET:
        cookies["auoreo_token"] = auoreo_token

    params: dict[str, Any] = {}

    json_token: None | str | Unset
    if isinstance(token, Unset):
        json_token = UNSET
    else:
        json_token = token
    params["token"] = json_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/rtc/offer",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | RtcAnswer | None:
    if response.status_code == 201:
        response_201 = RtcAnswer.from_dict(response.json())

        return response_201

    if response.status_code == 413:
        response_413 = cast(Any, None)
        return response_413

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if response.status_code == 501:
        response_501 = cast(Any, None)
        return response_501

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | RtcAnswer]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: RtcOffer,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | RtcAnswer]:
    """Offer

     Answer an SDP offer (non-trickle: send the offer after ICE gathering completes).

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (RtcOffer): Client SDP offer. The client must create the `auoreo-events` data channel
            first.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | RtcAnswer]
    """

    kwargs = _get_kwargs(
        body=body,
        token=token,
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
    body: RtcOffer,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | RtcAnswer | None:
    """Offer

     Answer an SDP offer (non-trickle: send the offer after ICE gathering completes).

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (RtcOffer): Client SDP offer. The client must create the `auoreo-events` data channel
            first.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | RtcAnswer
    """

    return sync_detailed(
        client=client,
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: RtcOffer,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | RtcAnswer]:
    """Offer

     Answer an SDP offer (non-trickle: send the offer after ICE gathering completes).

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (RtcOffer): Client SDP offer. The client must create the `auoreo-events` data channel
            first.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | RtcAnswer]
    """

    kwargs = _get_kwargs(
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: RtcOffer,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | RtcAnswer | None:
    """Offer

     Answer an SDP offer (non-trickle: send the offer after ICE gathering completes).

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (RtcOffer): Client SDP offer. The client must create the `auoreo-events` data channel
            first.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | RtcAnswer
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
