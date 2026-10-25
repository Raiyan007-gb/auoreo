from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.delivery_replay import DeliveryReplay
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    delivery_id: int,
    *,
    dry_run: bool | Unset = True,
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

    params: dict[str, Any] = {}

    params["dry_run"] = dry_run

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/deliveries/{delivery_id}/replay".format(
            delivery_id=quote(str(delivery_id), safe=""),
        ),
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeliveryReplay | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = DeliveryReplay.from_dict(response.json())

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
) -> Response[DeliveryReplay | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    delivery_id: int,
    *,
    client: AuthenticatedClient | Client,
    dry_run: bool | Unset = True,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[DeliveryReplay | HTTPValidationError]:
    """Replay Delivery

     `dry_run=true` (default) only shows the stored prompt; otherwise a new pending row
    with the same payload is queued (the original is untouched).

    Args:
        delivery_id (int):
        dry_run (bool | Unset):  Default: True.
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeliveryReplay | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        delivery_id=delivery_id,
        dry_run=dry_run,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    delivery_id: int,
    *,
    client: AuthenticatedClient | Client,
    dry_run: bool | Unset = True,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> DeliveryReplay | HTTPValidationError | None:
    """Replay Delivery

     `dry_run=true` (default) only shows the stored prompt; otherwise a new pending row
    with the same payload is queued (the original is untouched).

    Args:
        delivery_id (int):
        dry_run (bool | Unset):  Default: True.
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeliveryReplay | HTTPValidationError
    """

    return sync_detailed(
        delivery_id=delivery_id,
        client=client,
        dry_run=dry_run,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    delivery_id: int,
    *,
    client: AuthenticatedClient | Client,
    dry_run: bool | Unset = True,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[DeliveryReplay | HTTPValidationError]:
    """Replay Delivery

     `dry_run=true` (default) only shows the stored prompt; otherwise a new pending row
    with the same payload is queued (the original is untouched).

    Args:
        delivery_id (int):
        dry_run (bool | Unset):  Default: True.
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeliveryReplay | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        delivery_id=delivery_id,
        dry_run=dry_run,
        authorization=authorization,
        idempotency_key=idempotency_key,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    delivery_id: int,
    *,
    client: AuthenticatedClient | Client,
    dry_run: bool | Unset = True,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> DeliveryReplay | HTTPValidationError | None:
    """Replay Delivery

     `dry_run=true` (default) only shows the stored prompt; otherwise a new pending row
    with the same payload is queued (the original is untouched).

    Args:
        delivery_id (int):
        dry_run (bool | Unset):  Default: True.
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset): UUID; a retry with the same key replays the first
            response
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeliveryReplay | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            delivery_id=delivery_id,
            client=client,
            dry_run=dry_run,
            authorization=authorization,
            idempotency_key=idempotency_key,
            auoreo_token=auoreo_token,
        )
    ).parsed
