from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.fact_action_result import FactActionResult
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    fact_id: int,
    *,
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
        "url": "/api/memory/facts/{fact_id}/forget".format(
            fact_id=quote(str(fact_id), safe=""),
        ),
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> FactActionResult | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = FactActionResult.from_dict(response.json())

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
) -> Response[FactActionResult | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    fact_id: int,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[FactActionResult | HTTPValidationError]:
    """Forget Fact

     Archive a fact (never deleted). `ok` is false when it was already archived.

    Args:
        fact_id (int):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FactActionResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        fact_id=fact_id,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    fact_id: int,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> FactActionResult | HTTPValidationError | None:
    """Forget Fact

     Archive a fact (never deleted). `ok` is false when it was already archived.

    Args:
        fact_id (int):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FactActionResult | HTTPValidationError
    """

    return sync_detailed(
        fact_id=fact_id,
        client=client,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    fact_id: int,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[FactActionResult | HTTPValidationError]:
    """Forget Fact

     Archive a fact (never deleted). `ok` is false when it was already archived.

    Args:
        fact_id (int):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FactActionResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        fact_id=fact_id,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    fact_id: int,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> FactActionResult | HTTPValidationError | None:
    """Forget Fact

     Archive a fact (never deleted). `ok` is false when it was already archived.

    Args:
        fact_id (int):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FactActionResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            fact_id=fact_id,
            client=client,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
