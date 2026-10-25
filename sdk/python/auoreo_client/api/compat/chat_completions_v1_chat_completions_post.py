from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.open_ai_chat_completion import OpenAIChatCompletion
from ...models.open_ai_chat_request import OpenAIChatRequest
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: OpenAIChatRequest,
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
        "url": "/v1/chat/completions",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | OpenAIChatCompletion | None:
    if response.status_code == 200:
        response_200 = OpenAIChatCompletion.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = cast(Any, None)
        return response_400

    if response.status_code == 402:
        response_402 = cast(Any, None)
        return response_402

    if response.status_code == 404:
        response_404 = cast(Any, None)
        return response_404

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if response.status_code == 502:
        response_502 = cast(Any, None)
        return response_502

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | OpenAIChatCompletion]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: OpenAIChatRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | OpenAIChatCompletion]:
    """Chat Completions

     Non-streaming → a `chat.completion` object; `stream: true` → SSE `chat.completion.chunk`
    lines and `data: [DONE]`. Tool calls are returned, never executed.

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (OpenAIChatRequest): `POST /v1/chat/completions` body. Unknown OpenAI fields (`n`,
            `top_p`, …) are
            accepted and ignored; tools are the client's — nothing is executed server-side.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | OpenAIChatCompletion]
    """

    kwargs = _get_kwargs(
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
    *,
    client: AuthenticatedClient | Client,
    body: OpenAIChatRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | OpenAIChatCompletion | None:
    """Chat Completions

     Non-streaming → a `chat.completion` object; `stream: true` → SSE `chat.completion.chunk`
    lines and `data: [DONE]`. Tool calls are returned, never executed.

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (OpenAIChatRequest): `POST /v1/chat/completions` body. Unknown OpenAI fields (`n`,
            `top_p`, …) are
            accepted and ignored; tools are the client's — nothing is executed server-side.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | OpenAIChatCompletion
    """

    return sync_detailed(
        client=client,
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: OpenAIChatRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError | OpenAIChatCompletion]:
    """Chat Completions

     Non-streaming → a `chat.completion` object; `stream: true` → SSE `chat.completion.chunk`
    lines and `data: [DONE]`. Tool calls are returned, never executed.

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (OpenAIChatRequest): `POST /v1/chat/completions` body. Unknown OpenAI fields (`n`,
            `top_p`, …) are
            accepted and ignored; tools are the client's — nothing is executed server-side.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | OpenAIChatCompletion]
    """

    kwargs = _get_kwargs(
        body=body,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: OpenAIChatRequest,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | OpenAIChatCompletion | None:
    """Chat Completions

     Non-streaming → a `chat.completion` object; `stream: true` → SSE `chat.completion.chunk`
    lines and `data: [DONE]`. Tool calls are returned, never executed.

    Args:
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (OpenAIChatRequest): `POST /v1/chat/completions` body. Unknown OpenAI fields (`n`,
            `top_p`, …) are
            accepted and ignored; tools are the client's — nothing is executed server-side.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | OpenAIChatCompletion
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
