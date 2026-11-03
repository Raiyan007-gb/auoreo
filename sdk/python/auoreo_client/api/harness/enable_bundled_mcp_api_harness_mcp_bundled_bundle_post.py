from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.bundled_mcp_result import BundledMcpResult
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    bundle: str,
    *,
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
        "url": "/api/harness/mcp/bundled/{bundle}".format(
            bundle=quote(str(bundle), safe=""),
        ),
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BundledMcpResult | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = BundledMcpResult.from_dict(response.json())

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
) -> Response[BundledMcpResult | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    bundle: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[BundledMcpResult | HTTPValidationError]:
    """Enable Bundled Mcp

     Turn a bundled MCP server on: copy `mcp/<bundle>.yaml` into `<home>/mcp/` with
    `enabled: true`, then reload the `mcp` kind (the API twin of `auoreo mcp add --bundle`).

    Args:
        bundle (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BundledMcpResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        bundle=bundle,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    bundle: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> BundledMcpResult | HTTPValidationError | None:
    """Enable Bundled Mcp

     Turn a bundled MCP server on: copy `mcp/<bundle>.yaml` into `<home>/mcp/` with
    `enabled: true`, then reload the `mcp` kind (the API twin of `auoreo mcp add --bundle`).

    Args:
        bundle (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BundledMcpResult | HTTPValidationError
    """

    return sync_detailed(
        bundle=bundle,
        client=client,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    bundle: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[BundledMcpResult | HTTPValidationError]:
    """Enable Bundled Mcp

     Turn a bundled MCP server on: copy `mcp/<bundle>.yaml` into `<home>/mcp/` with
    `enabled: true`, then reload the `mcp` kind (the API twin of `auoreo mcp add --bundle`).

    Args:
        bundle (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BundledMcpResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        bundle=bundle,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    bundle: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> BundledMcpResult | HTTPValidationError | None:
    """Enable Bundled Mcp

     Turn a bundled MCP server on: copy `mcp/<bundle>.yaml` into `<home>/mcp/` with
    `enabled: true`, then reload the `mcp` kind (the API twin of `auoreo mcp add --bundle`).

    Args:
        bundle (str):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BundledMcpResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            bundle=bundle,
            client=client,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
