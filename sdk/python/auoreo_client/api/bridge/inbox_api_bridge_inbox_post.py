from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.bridge_delivered import BridgeDelivered
from ...models.bridge_envelope import BridgeEnvelope
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: BridgeEnvelope,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/bridge/inbox",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | BridgeDelivered | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = BridgeDelivered.from_dict(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = cast(Any, None)
        return response_401

    if response.status_code == 403:
        response_403 = cast(Any, None)
        return response_403

    if response.status_code == 404:
        response_404 = cast(Any, None)
        return response_404

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
) -> Response[Any | BridgeDelivered | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BridgeEnvelope,
) -> Response[Any | BridgeDelivered | HTTPValidationError]:
    """Inbox

     Verify `X-Auoreo-Signature` and deliver into the local team mailbox as
    `from: bridge:<sender>/<member>`. The signature covers the raw body bytes.

    Args:
        body (BridgeEnvelope): `POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
            `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | BridgeDelivered | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: BridgeEnvelope,
) -> Any | BridgeDelivered | HTTPValidationError | None:
    """Inbox

     Verify `X-Auoreo-Signature` and deliver into the local team mailbox as
    `from: bridge:<sender>/<member>`. The signature covers the raw body bytes.

    Args:
        body (BridgeEnvelope): `POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
            `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | BridgeDelivered | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BridgeEnvelope,
) -> Response[Any | BridgeDelivered | HTTPValidationError]:
    """Inbox

     Verify `X-Auoreo-Signature` and deliver into the local team mailbox as
    `from: bridge:<sender>/<member>`. The signature covers the raw body bytes.

    Args:
        body (BridgeEnvelope): `POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
            `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | BridgeDelivered | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: BridgeEnvelope,
) -> Any | BridgeDelivered | HTTPValidationError | None:
    """Inbox

     Verify `X-Auoreo-Signature` and deliver into the local team mailbox as
    `from: bridge:<sender>/<member>`. The signature covers the raw body bytes.

    Args:
        body (BridgeEnvelope): `POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
            `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | BridgeDelivered | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
