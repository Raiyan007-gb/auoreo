from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.session_detail import SessionDetail
from ...types import UNSET, Response, Unset


def _get_kwargs(
    session_id: str,
    *,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    if not isinstance(idempotency_key, Unset):
        headers["idempotency-key"] = idempotency_key

    cookies = {}
    if auoreo_token is not UNSET:
        cookies["auoreo_token"] = auoreo_token

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/sessions/{session_id}/fork".format(
            session_id=quote(str(session_id), safe=""),
        ),
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SessionDetail | None:
    if response.status_code == 201:
        response_201 = SessionDetail.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | SessionDetail]:
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
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionDetail]:
    """Fork

     New session (same agent/workflow/model, `parent_id` set) with every committed message.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionDetail]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        authorization=authorization,
        idempotency_key=idempotency_key,
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
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionDetail | None:
    """Fork

     New session (same agent/workflow/model, `parent_id` set) with every committed message.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionDetail
    """

    return sync_detailed(
        session_id=session_id,
        client=client,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionDetail]:
    """Fork

     New session (same agent/workflow/model, `parent_id` set) with every committed message.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionDetail]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionDetail | None:
    """Fork

     New session (same agent/workflow/model, `parent_id` set) with every committed message.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionDetail
    """

    return (
        await asyncio_detailed(
            session_id=session_id,
            client=client,
            authorization=authorization,
            idempotency_key=idempotency_key,
            auoreo_token=auoreo_token,
        )
    ).parsed
