from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.meeting_attach_request import MeetingAttachRequest
from ...models.meeting_info import MeetingInfo
from ...types import UNSET, Response, Unset


def _get_kwargs(
    meeting_id: str,
    *,
    body: MeetingAttachRequest,
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
        "url": "/api/voice/meetings/{meeting_id}/attach".format(
            meeting_id=quote(str(meeting_id), safe=""),
        ),
        "params": params,
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | MeetingInfo | None:
    if response.status_code == 200:
        response_200 = MeetingInfo.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = cast(Any, None)
        return response_404

    if response.status_code == 409:
        response_409 = cast(Any, None)
        return response_409

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 503:
        response_503 = cast(Any, None)
        return response_503

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | MeetingInfo]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    meeting_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MeetingAttachRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | MeetingInfo]:
    """Attach Peer

     Live mode: utterances from this RTC peer are appended instead of submitted as turns.

    Args:
        meeting_id (str):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (MeetingAttachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | MeetingInfo]
    """

    kwargs = _get_kwargs(
        meeting_id=meeting_id,
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
    meeting_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MeetingAttachRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | MeetingInfo | None:
    """Attach Peer

     Live mode: utterances from this RTC peer are appended instead of submitted as turns.

    Args:
        meeting_id (str):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (MeetingAttachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | MeetingInfo
    """

    return sync_detailed(
        meeting_id=meeting_id,
        client=client,
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    meeting_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MeetingAttachRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | MeetingInfo]:
    """Attach Peer

     Live mode: utterances from this RTC peer are appended instead of submitted as turns.

    Args:
        meeting_id (str):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (MeetingAttachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | MeetingInfo]
    """

    kwargs = _get_kwargs(
        meeting_id=meeting_id,
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    meeting_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MeetingAttachRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | MeetingInfo | None:
    """Attach Peer

     Live mode: utterances from this RTC peer are appended instead of submitted as turns.

    Args:
        meeting_id (str):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (MeetingAttachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | MeetingInfo
    """

    return (
        await asyncio_detailed(
            meeting_id=meeting_id,
            client=client,
            body=body,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
