from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.memory_fact_page import MemoryFactPage
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    query: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    pinned: bool | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
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

    json_query: None | str | Unset
    if isinstance(query, Unset):
        json_query = UNSET
    else:
        json_query = query
    params["query"] = json_query

    params["include_archived"] = include_archived

    json_pinned: bool | None | Unset
    if isinstance(pinned, Unset):
        json_pinned = UNSET
    else:
        json_pinned = pinned
    params["pinned"] = json_pinned

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/memory/facts",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | MemoryFactPage | None:
    if response.status_code == 200:
        response_200 = MemoryFactPage.from_dict(response.json())

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
) -> Response[HTTPValidationError | MemoryFactPage]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    pinned: bool | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | MemoryFactPage]:
    """List Facts

     Facts newest first. `query` is a case-insensitive substring; archived/expired facts
    appear only with `include_archived`.

    Args:
        query (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        pinned (bool | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | MemoryFactPage]
    """

    kwargs = _get_kwargs(
        query=query,
        include_archived=include_archived,
        pinned=pinned,
        limit=limit,
        offset=offset,
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
    query: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    pinned: bool | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | MemoryFactPage | None:
    """List Facts

     Facts newest first. `query` is a case-insensitive substring; archived/expired facts
    appear only with `include_archived`.

    Args:
        query (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        pinned (bool | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | MemoryFactPage
    """

    return sync_detailed(
        client=client,
        query=query,
        include_archived=include_archived,
        pinned=pinned,
        limit=limit,
        offset=offset,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    pinned: bool | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | MemoryFactPage]:
    """List Facts

     Facts newest first. `query` is a case-insensitive substring; archived/expired facts
    appear only with `include_archived`.

    Args:
        query (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        pinned (bool | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | MemoryFactPage]
    """

    kwargs = _get_kwargs(
        query=query,
        include_archived=include_archived,
        pinned=pinned,
        limit=limit,
        offset=offset,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    pinned: bool | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | MemoryFactPage | None:
    """List Facts

     Facts newest first. `query` is a case-insensitive substring; archived/expired facts
    appear only with `include_archived`.

    Args:
        query (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        pinned (bool | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | MemoryFactPage
    """

    return (
        await asyncio_detailed(
            client=client,
            query=query,
            include_archived=include_archived,
            pinned=pinned,
            limit=limit,
            offset=offset,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
