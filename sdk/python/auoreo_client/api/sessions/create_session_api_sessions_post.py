from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.session_create import SessionCreate
from ...models.session_info import SessionInfo
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: SessionCreate,
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
        "url": "/api/sessions",
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SessionInfo | None:
    if response.status_code == 201:
        response_201 = SessionInfo.from_dict(response.json())

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
) -> Response[HTTPValidationError | SessionInfo]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SessionCreate,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionInfo]:
    """Create Session

    Args:
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):
        body (SessionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionInfo]
    """

    kwargs = _get_kwargs(
        body=body,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: SessionCreate,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionInfo | None:
    """Create Session

    Args:
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):
        body (SessionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionInfo
    """

    return sync_detailed(
        client=client,
        body=body,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SessionCreate,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionInfo]:
    """Create Session

    Args:
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):
        body (SessionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionInfo]
    """

    kwargs = _get_kwargs(
        body=body,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: SessionCreate,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionInfo | None:
    """Create Session

    Args:
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):
        body (SessionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionInfo
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            authorization=authorization,
            idempotency_key=idempotency_key,
            auoreo_token=auoreo_token,
        )
    ).parsed
