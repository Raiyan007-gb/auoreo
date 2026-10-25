from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    follow: bool | Unset = True,
    after_seq: int | Unset = 0,
    session_id: list[str] | None | Unset = UNSET,
    channels: list[str] | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    if not isinstance(last_event_id, Unset):
        headers["last-event-id"] = last_event_id

    cookies = {}
    if auoreo_token is not UNSET:
        cookies["auoreo_token"] = auoreo_token

    params: dict[str, Any] = {}

    params["follow"] = follow

    params["after_seq"] = after_seq

    json_session_id: list[str] | None | Unset
    if isinstance(session_id, Unset):
        json_session_id = UNSET
    elif isinstance(session_id, list):
        json_session_id = session_id

    else:
        json_session_id = session_id
    params["session_id"] = json_session_id

    json_channels: list[str] | None | Unset
    if isinstance(channels, Unset):
        json_channels = UNSET
    elif isinstance(channels, list):
        json_channels = channels

    else:
        json_channels = channels
    params["channels"] = json_channels

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/events",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = cast(Any, None)
        return response_200

    if response.status_code == 206:
        response_206 = cast(Any, None)
        return response_206

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    follow: bool | Unset = True,
    after_seq: int | Unset = 0,
    session_id: list[str] | None | Unset = UNSET,
    channels: list[str] | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Events

     `Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=` or
    `?channels=` (`session:<id>`, `job:<id>`, `server`). `follow=false` returns the replay
    only. A replay over `http.replay_cap` answers 206 and ends with `cap_warning`: reconnect
    with `Last-Event-ID` to continue.

    Args:
        follow (bool | Unset):  Default: True.
        after_seq (int | Unset):  Default: 0.
        session_id (list[str] | None | Unset):
        channels (list[str] | None | Unset): `session:<id>`, `job:<id>`, `server` (comma-
            separated)
        authorization (None | str | Unset):
        last_event_id (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        follow=follow,
        after_seq=after_seq,
        session_id=session_id,
        channels=channels,
        authorization=authorization,
        last_event_id=last_event_id,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    follow: bool | Unset = True,
    after_seq: int | Unset = 0,
    session_id: list[str] | None | Unset = UNSET,
    channels: list[str] | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Events

     `Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=` or
    `?channels=` (`session:<id>`, `job:<id>`, `server`). `follow=false` returns the replay
    only. A replay over `http.replay_cap` answers 206 and ends with `cap_warning`: reconnect
    with `Last-Event-ID` to continue.

    Args:
        follow (bool | Unset):  Default: True.
        after_seq (int | Unset):  Default: 0.
        session_id (list[str] | None | Unset):
        channels (list[str] | None | Unset): `session:<id>`, `job:<id>`, `server` (comma-
            separated)
        authorization (None | str | Unset):
        last_event_id (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        follow=follow,
        after_seq=after_seq,
        session_id=session_id,
        channels=channels,
        authorization=authorization,
        last_event_id=last_event_id,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    follow: bool | Unset = True,
    after_seq: int | Unset = 0,
    session_id: list[str] | None | Unset = UNSET,
    channels: list[str] | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Events

     `Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=` or
    `?channels=` (`session:<id>`, `job:<id>`, `server`). `follow=false` returns the replay
    only. A replay over `http.replay_cap` answers 206 and ends with `cap_warning`: reconnect
    with `Last-Event-ID` to continue.

    Args:
        follow (bool | Unset):  Default: True.
        after_seq (int | Unset):  Default: 0.
        session_id (list[str] | None | Unset):
        channels (list[str] | None | Unset): `session:<id>`, `job:<id>`, `server` (comma-
            separated)
        authorization (None | str | Unset):
        last_event_id (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        follow=follow,
        after_seq=after_seq,
        session_id=session_id,
        channels=channels,
        authorization=authorization,
        last_event_id=last_event_id,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    follow: bool | Unset = True,
    after_seq: int | Unset = 0,
    session_id: list[str] | None | Unset = UNSET,
    channels: list[str] | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Events

     `Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=` or
    `?channels=` (`session:<id>`, `job:<id>`, `server`). `follow=false` returns the replay
    only. A replay over `http.replay_cap` answers 206 and ends with `cap_warning`: reconnect
    with `Last-Event-ID` to continue.

    Args:
        follow (bool | Unset):  Default: True.
        after_seq (int | Unset):  Default: 0.
        session_id (list[str] | None | Unset):
        channels (list[str] | None | Unset): `session:<id>`, `job:<id>`, `server` (comma-
            separated)
        authorization (None | str | Unset):
        last_event_id (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            follow=follow,
            after_seq=after_seq,
            session_id=session_id,
            channels=channels,
            authorization=authorization,
            last_event_id=last_event_id,
            auoreo_token=auoreo_token,
        )
    ).parsed
