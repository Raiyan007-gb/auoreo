from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.live_kit_token import LiveKitToken
from ...models.live_kit_token_request import LiveKitTokenRequest
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: LiveKitTokenRequest,
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
        "method": "post",
        "url": "/api/livekit/token",
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | LiveKitToken | None:
    if response.status_code == 201:
        response_201 = LiveKitToken.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = cast(Any, None)
        return response_400

    if response.status_code == 403:
        response_403 = cast(Any, None)
        return response_403

    if response.status_code == 404:
        response_404 = cast(Any, None)
        return response_404

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 501:
        response_501 = cast(Any, None)
        return response_501

    if response.status_code == 502:
        response_502 = cast(Any, None)
        return response_502

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | LiveKitToken]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: LiveKitTokenRequest,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | LiveKitToken]:
    """Token

     Mint a room token for the browser (`TokenSource.endpoint`); the JWT is never logged.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (LiveKitTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | LiveKitToken]
    """

    kwargs = _get_kwargs(
        body=body,
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
    body: LiveKitTokenRequest,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | LiveKitToken | None:
    """Token

     Mint a room token for the browser (`TokenSource.endpoint`); the JWT is never logged.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (LiveKitTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | LiveKitToken
    """

    return sync_detailed(
        client=client,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: LiveKitTokenRequest,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | LiveKitToken]:
    """Token

     Mint a room token for the browser (`TokenSource.endpoint`); the JWT is never logged.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (LiveKitTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | LiveKitToken]
    """

    kwargs = _get_kwargs(
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: LiveKitTokenRequest,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | LiveKitToken | None:
    """Token

     Mint a room token for the browser (`TokenSource.endpoint`); the JWT is never logged.

    Args:
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (LiveKitTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | LiveKitToken
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
