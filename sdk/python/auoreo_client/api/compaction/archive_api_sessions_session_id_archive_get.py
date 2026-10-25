from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.archived_message import ArchivedMessage
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    session_id: str,
    *,
    limit: int | Unset = 1000,
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

    params["limit"] = limit

    json_token: None | str | Unset
    if isinstance(token, Unset):
        json_token = UNSET
    else:
        json_token = token
    params["token"] = json_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/sessions/{session_id}/archive".format(
            session_id=quote(str(session_id), safe=""),
        ),
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[ArchivedMessage] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ArchivedMessage.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[HTTPValidationError | list[ArchivedMessage]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 1000,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ArchivedMessage]]:
    """Archive

     Archived messages, newest compaction first; original order within a compaction.

    Args:
        session_id (str):
        limit (int | Unset):  Default: 1000.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ArchivedMessage]]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        limit=limit,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 1000,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ArchivedMessage] | None:
    """Archive

     Archived messages, newest compaction first; original order within a compaction.

    Args:
        session_id (str):
        limit (int | Unset):  Default: 1000.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ArchivedMessage]
    """

    return sync_detailed(
        session_id=session_id,
        client=client,
        limit=limit,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 1000,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ArchivedMessage]]:
    """Archive

     Archived messages, newest compaction first; original order within a compaction.

    Args:
        session_id (str):
        limit (int | Unset):  Default: 1000.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ArchivedMessage]]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        limit=limit,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 1000,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ArchivedMessage] | None:
    """Archive

     Archived messages, newest compaction first; original order within a compaction.

    Args:
        session_id (str):
        limit (int | Unset):  Default: 1000.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ArchivedMessage]
    """

    return (
        await asyncio_detailed(
            session_id=session_id,
            client=client,
            limit=limit,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
