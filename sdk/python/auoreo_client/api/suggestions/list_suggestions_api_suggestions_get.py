from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_suggestions_api_suggestions_get_status_type_0 import (
    ListSuggestionsApiSuggestionsGetStatusType0,
)
from ...models.suggestion_info import SuggestionInfo
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
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
    elif isinstance(status, ListSuggestionsApiSuggestionsGetStatusType0):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/suggestions",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[SuggestionInfo] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = SuggestionInfo.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[SuggestionInfo]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[SuggestionInfo]]:
    """List Suggestions

    Args:
        status (ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[SuggestionInfo]]
    """

    kwargs = _get_kwargs(
        status=status,
        limit=limit,
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
    status: ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[SuggestionInfo] | None:
    """List Suggestions

    Args:
        status (ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[SuggestionInfo]
    """

    return sync_detailed(
        client=client,
        status=status,
        limit=limit,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[SuggestionInfo]]:
    """List Suggestions

    Args:
        status (ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[SuggestionInfo]]
    """

    kwargs = _get_kwargs(
        status=status,
        limit=limit,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    status: ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | list[SuggestionInfo] | None:
    """List Suggestions

    Args:
        status (ListSuggestionsApiSuggestionsGetStatusType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[SuggestionInfo]
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            limit=limit,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
