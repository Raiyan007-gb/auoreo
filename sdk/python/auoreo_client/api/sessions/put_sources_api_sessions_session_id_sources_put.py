from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.session_sources import SessionSources
from ...models.session_sources_in import SessionSourcesIn
from ...types import UNSET, Response, Unset


def _get_kwargs(
    session_id: str,
    *,
    body: SessionSourcesIn,
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
        "method": "put",
        "url": "/api/sessions/{session_id}/sources".format(
            session_id=quote(str(session_id), safe=""),
        ),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SessionSources | None:
    if response.status_code == 200:
        response_200 = SessionSources.from_dict(response.json())

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
) -> Response[HTTPValidationError | SessionSources]:
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
    body: SessionSourcesIn,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionSources]:
    """Put Sources

     Replace the session's disabled list; applies from the session's next turn.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (SessionSourcesIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionSources]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        body=body,
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
    body: SessionSourcesIn,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionSources | None:
    """Put Sources

     Replace the session's disabled list; applies from the session's next turn.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (SessionSourcesIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionSources
    """

    return sync_detailed(
        session_id=session_id,
        client=client,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionSourcesIn,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | SessionSources]:
    """Put Sources

     Replace the session's disabled list; applies from the session's next turn.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (SessionSourcesIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SessionSources]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionSourcesIn,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | SessionSources | None:
    """Put Sources

     Replace the session's disabled list; applies from the session's next turn.

    Args:
        session_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (SessionSourcesIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SessionSources
    """

    return (
        await asyncio_detailed(
            session_id=session_id,
            client=client,
            body=body,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
