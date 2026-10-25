from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.hook_ack import HookAck
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    route: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/hooks/github/{route}".format(
            route=quote(str(route), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | HookAck | None:
    if response.status_code == 200:
        response_200 = HookAck.from_dict(response.json())

        return response_200

    if response.status_code == 202:
        response_202 = HookAck.from_dict(response.json())

        return response_202

    if response.status_code == 400:
        response_400 = cast(Any, None)
        return response_400

    if response.status_code == 401:
        response_401 = cast(Any, None)
        return response_401

    if response.status_code == 403:
        response_403 = cast(Any, None)
        return response_403

    if response.status_code == 404:
        response_404 = cast(Any, None)
        return response_404

    if response.status_code == 413:
        response_413 = cast(Any, None)
        return response_413

    if response.status_code == 415:
        response_415 = cast(Any, None)
        return response_415

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if response.status_code == 502:
        response_502 = cast(Any, None)
        return response_502

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | HookAck]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    route: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | HTTPValidationError | HookAck]:
    """Receive Github Hook

     Alias of `/hooks/{route}` that only matches `provider: github` routes.

    Args:
        route (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | HookAck]
    """

    kwargs = _get_kwargs(
        route=route,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    route: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | HTTPValidationError | HookAck | None:
    """Receive Github Hook

     Alias of `/hooks/{route}` that only matches `provider: github` routes.

    Args:
        route (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | HookAck
    """

    return sync_detailed(
        route=route,
        client=client,
    ).parsed


async def asyncio_detailed(
    route: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | HTTPValidationError | HookAck]:
    """Receive Github Hook

     Alias of `/hooks/{route}` that only matches `provider: github` routes.

    Args:
        route (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | HookAck]
    """

    kwargs = _get_kwargs(
        route=route,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    route: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | HTTPValidationError | HookAck | None:
    """Receive Github Hook

     Alias of `/hooks/{route}` that only matches `provider: github` routes.

    Args:
        route (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | HookAck
    """

    return (
        await asyncio_detailed(
            route=route,
            client=client,
        )
    ).parsed
