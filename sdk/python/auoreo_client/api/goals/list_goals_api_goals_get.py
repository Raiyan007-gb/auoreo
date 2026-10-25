from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.goal_info import GoalInfo
from ...models.http_validation_error import HTTPValidationError
from ...models.list_goals_api_goals_get_status_type_0 import ListGoalsApiGoalsGetStatusType0
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: ListGoalsApiGoalsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
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

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, ListGoalsApiGoalsGetStatusType0):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status

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
        "url": "/api/goals",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[GoalInfo] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = GoalInfo.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[GoalInfo]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: ListGoalsApiGoalsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[GoalInfo]]:
    """List Goals

     No `status` lists every goal; otherwise only that status.

    Args:
        status (ListGoalsApiGoalsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GoalInfo]]
    """

    kwargs = _get_kwargs(
        status=status,
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
    *,
    client: AuthenticatedClient | Client,
    status: ListGoalsApiGoalsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[GoalInfo] | None:
    """List Goals

     No `status` lists every goal; otherwise only that status.

    Args:
        status (ListGoalsApiGoalsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GoalInfo]
    """

    return sync_detailed(
        client=client,
        status=status,
        limit=limit,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: ListGoalsApiGoalsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[GoalInfo]]:
    """List Goals

     No `status` lists every goal; otherwise only that status.

    Args:
        status (ListGoalsApiGoalsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GoalInfo]]
    """

    kwargs = _get_kwargs(
        status=status,
        limit=limit,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    status: ListGoalsApiGoalsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[GoalInfo] | None:
    """List Goals

     No `status` lists every goal; otherwise only that status.

    Args:
        status (ListGoalsApiGoalsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GoalInfo]
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            limit=limit,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
