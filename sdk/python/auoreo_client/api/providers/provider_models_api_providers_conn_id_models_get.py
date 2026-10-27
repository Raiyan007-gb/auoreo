from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.provider_models import ProviderModels
from ...models.provider_models_api_providers_conn_id_models_get_kind_type_0 import (
    ProviderModelsApiProvidersConnIdModelsGetKindType0,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    conn_id: int,
    *,
    kind: None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset = UNSET,
    q: None | str | Unset = UNSET,
    refresh: int | Unset = 0,
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

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, ProviderModelsApiProvidersConnIdModelsGetKindType0):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    params["refresh"] = refresh

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/providers/{conn_id}/models".format(
            conn_id=quote(str(conn_id), safe=""),
        ),
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | ProviderModels | None:
    if response.status_code == 200:
        response_200 = ProviderModels.from_dict(response.json())

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
) -> Response[HTTPValidationError | ProviderModels]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    conn_id: int,
    *,
    client: AuthenticatedClient | Client,
    kind: None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset = UNSET,
    q: None | str | Unset = UNSET,
    refresh: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | ProviderModels]:
    """Provider Models

     The cached list (refetched when `refresh=1` or older than 1 h; a failed fetch keeps it).

    Args:
        conn_id (int):
        kind (None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset):
        q (None | str | Unset):
        refresh (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ProviderModels]
    """

    kwargs = _get_kwargs(
        conn_id=conn_id,
        kind=kind,
        q=q,
        refresh=refresh,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    conn_id: int,
    *,
    client: AuthenticatedClient | Client,
    kind: None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset = UNSET,
    q: None | str | Unset = UNSET,
    refresh: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | ProviderModels | None:
    """Provider Models

     The cached list (refetched when `refresh=1` or older than 1 h; a failed fetch keeps it).

    Args:
        conn_id (int):
        kind (None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset):
        q (None | str | Unset):
        refresh (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ProviderModels
    """

    return sync_detailed(
        conn_id=conn_id,
        client=client,
        kind=kind,
        q=q,
        refresh=refresh,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    conn_id: int,
    *,
    client: AuthenticatedClient | Client,
    kind: None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset = UNSET,
    q: None | str | Unset = UNSET,
    refresh: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | ProviderModels]:
    """Provider Models

     The cached list (refetched when `refresh=1` or older than 1 h; a failed fetch keeps it).

    Args:
        conn_id (int):
        kind (None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset):
        q (None | str | Unset):
        refresh (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ProviderModels]
    """

    kwargs = _get_kwargs(
        conn_id=conn_id,
        kind=kind,
        q=q,
        refresh=refresh,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    conn_id: int,
    *,
    client: AuthenticatedClient | Client,
    kind: None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset = UNSET,
    q: None | str | Unset = UNSET,
    refresh: int | Unset = 0,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | ProviderModels | None:
    """Provider Models

     The cached list (refetched when `refresh=1` or older than 1 h; a failed fetch keeps it).

    Args:
        conn_id (int):
        kind (None | ProviderModelsApiProvidersConnIdModelsGetKindType0 | Unset):
        q (None | str | Unset):
        refresh (int | Unset):  Default: 0.
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ProviderModels
    """

    return (
        await asyncio_detailed(
            conn_id=conn_id,
            client=client,
            kind=kind,
            q=q,
            refresh=refresh,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
