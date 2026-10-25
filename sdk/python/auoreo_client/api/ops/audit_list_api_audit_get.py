from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.audit_page import AuditPage
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    session_id: None | str | Unset = UNSET,
    action: None | str | Unset = UNSET,
    newest_first: bool | Unset = False,
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

    json_after_seq: int | None | Unset
    if isinstance(after_seq, Unset):
        json_after_seq = UNSET
    else:
        json_after_seq = after_seq
    params["after_seq"] = json_after_seq

    json_before_seq: int | None | Unset
    if isinstance(before_seq, Unset):
        json_before_seq = UNSET
    else:
        json_before_seq = before_seq
    params["before_seq"] = json_before_seq

    params["limit"] = limit

    json_session_id: None | str | Unset
    if isinstance(session_id, Unset):
        json_session_id = UNSET
    else:
        json_session_id = session_id
    params["session_id"] = json_session_id

    json_action: None | str | Unset
    if isinstance(action, Unset):
        json_action = UNSET
    else:
        json_action = action
    params["action"] = json_action

    params["newest_first"] = newest_first

    json_token: None | str | Unset
    if isinstance(token, Unset):
        json_token = UNSET
    else:
        json_token = token
    params["token"] = json_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/audit",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AuditPage | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AuditPage.from_dict(response.json())

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
) -> Response[AuditPage | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    session_id: None | str | Unset = UNSET,
    action: None | str | Unset = UNSET,
    newest_first: bool | Unset = False,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[AuditPage | HTTPValidationError]:
    """Audit List

     Audit rows in seq order (`newest_first` reverses it). Filters combine with AND.

    Args:
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        limit (int | Unset):  Default: 100.
        session_id (None | str | Unset):
        action (None | str | Unset):
        newest_first (bool | Unset):  Default: False.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditPage | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        after_seq=after_seq,
        before_seq=before_seq,
        limit=limit,
        session_id=session_id,
        action=action,
        newest_first=newest_first,
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
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    session_id: None | str | Unset = UNSET,
    action: None | str | Unset = UNSET,
    newest_first: bool | Unset = False,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> AuditPage | HTTPValidationError | None:
    """Audit List

     Audit rows in seq order (`newest_first` reverses it). Filters combine with AND.

    Args:
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        limit (int | Unset):  Default: 100.
        session_id (None | str | Unset):
        action (None | str | Unset):
        newest_first (bool | Unset):  Default: False.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditPage | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        after_seq=after_seq,
        before_seq=before_seq,
        limit=limit,
        session_id=session_id,
        action=action,
        newest_first=newest_first,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    session_id: None | str | Unset = UNSET,
    action: None | str | Unset = UNSET,
    newest_first: bool | Unset = False,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[AuditPage | HTTPValidationError]:
    """Audit List

     Audit rows in seq order (`newest_first` reverses it). Filters combine with AND.

    Args:
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        limit (int | Unset):  Default: 100.
        session_id (None | str | Unset):
        action (None | str | Unset):
        newest_first (bool | Unset):  Default: False.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditPage | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        after_seq=after_seq,
        before_seq=before_seq,
        limit=limit,
        session_id=session_id,
        action=action,
        newest_first=newest_first,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    session_id: None | str | Unset = UNSET,
    action: None | str | Unset = UNSET,
    newest_first: bool | Unset = False,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> AuditPage | HTTPValidationError | None:
    """Audit List

     Audit rows in seq order (`newest_first` reverses it). Filters combine with AND.

    Args:
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        limit (int | Unset):  Default: 100.
        session_id (None | str | Unset):
        action (None | str | Unset):
        newest_first (bool | Unset):  Default: False.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditPage | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
            session_id=session_id,
            action=action,
            newest_first=newest_first,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
