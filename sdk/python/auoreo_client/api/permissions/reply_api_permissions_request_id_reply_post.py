from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.permission_reply import PermissionReply
from ...models.reply_api_permissions_request_id_reply_post_response_reply_api_permissions_request_id_reply_post import (
    ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    request_id: str,
    *,
    body: PermissionReply,
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
        "url": "/api/permissions/{request_id}/reply".format(
            request_id=quote(str(request_id), safe=""),
        ),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost | None:
    if response.status_code == 200:
        response_200 = (
            ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost.from_dict(
                response.json()
            )
        )

        return response_200

    if response.status_code == 422:
        response_422 = cast(Any, None)
        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    request_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PermissionReply,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost]:
    """Reply

     `once|always|reject`; `updated_input` allows an `editable` request with edited arguments.

    Args:
        request_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (PermissionReply):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    request_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PermissionReply,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost | None:
    """Reply

     `once|always|reject`; `updated_input` allows an `editable` request with edited arguments.

    Args:
        request_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (PermissionReply):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost
    """

    return sync_detailed(
        request_id=request_id,
        client=client,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    request_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PermissionReply,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost]:
    """Reply

     `once|always|reject`; `updated_input` allows an `editable` request with edited arguments.

    Args:
        request_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (PermissionReply):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    request_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PermissionReply,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost | None:
    """Reply

     `once|always|reject`; `updated_input` allows an `editable` request with edited arguments.

    Args:
        request_id (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (PermissionReply):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ReplyApiPermissionsRequestIdReplyPostResponseReplyApiPermissionsRequestIdReplyPost
    """

    return (
        await asyncio_detailed(
            request_id=request_id,
            client=client,
            body=body,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
