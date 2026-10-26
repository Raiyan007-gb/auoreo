from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.env_applied import EnvApplied
from ...models.env_secret_write import EnvSecretWrite
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    name: str,
    *,
    body: EnvSecretWrite,
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
        "method": "put",
        "url": "/api/env/secrets/{name}".format(
            name=quote(str(name), safe=""),
        ),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvApplied | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EnvApplied.from_dict(response.json())

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
) -> Response[EnvApplied | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: EnvSecretWrite,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[EnvApplied | HTTPValidationError]:
    """Put Secret

     Store a catalog secret (write-only; the response never echoes it).

    Args:
        name (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (EnvSecretWrite):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvApplied | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: EnvSecretWrite,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> EnvApplied | HTTPValidationError | None:
    """Put Secret

     Store a catalog secret (write-only; the response never echoes it).

    Args:
        name (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (EnvSecretWrite):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvApplied | HTTPValidationError
    """

    return sync_detailed(
        name=name,
        client=client,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: EnvSecretWrite,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[EnvApplied | HTTPValidationError]:
    """Put Secret

     Store a catalog secret (write-only; the response never echoes it).

    Args:
        name (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (EnvSecretWrite):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvApplied | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: EnvSecretWrite,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> EnvApplied | HTTPValidationError | None:
    """Put Secret

     Store a catalog secret (write-only; the response never echoes it).

    Args:
        name (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (EnvSecretWrite):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvApplied | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            name=name,
            client=client,
            body=body,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
