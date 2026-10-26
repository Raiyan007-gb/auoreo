from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.timeseries_api_usage_timeseries_get_bucket_type_0 import (
    TimeseriesApiUsageTimeseriesGetBucketType0,
)
from ...models.usage_series import UsageSeries
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    days: int | Unset = 7,
    bucket: None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset = UNSET,
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

    params["days"] = days

    json_bucket: None | str | Unset
    if isinstance(bucket, Unset):
        json_bucket = UNSET
    elif isinstance(bucket, TimeseriesApiUsageTimeseriesGetBucketType0):
        json_bucket = bucket.value
    else:
        json_bucket = bucket
    params["bucket"] = json_bucket

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/usage/timeseries",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UsageSeries | None:
    if response.status_code == 200:
        response_200 = UsageSeries.from_dict(response.json())

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
) -> Response[HTTPValidationError | UsageSeries]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 7,
    bucket: None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UsageSeries]:
    """Timeseries

     Token/cost chart data plus the budget guards (`max_cost_usd`, per-turn loop budgets).

    Args:
        days (int | Unset):  Default: 7.
        bucket (None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UsageSeries]
    """

    kwargs = _get_kwargs(
        days=days,
        bucket=bucket,
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
    days: int | Unset = 7,
    bucket: None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | UsageSeries | None:
    """Timeseries

     Token/cost chart data plus the budget guards (`max_cost_usd`, per-turn loop budgets).

    Args:
        days (int | Unset):  Default: 7.
        bucket (None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UsageSeries
    """

    return sync_detailed(
        client=client,
        days=days,
        bucket=bucket,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 7,
    bucket: None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UsageSeries]:
    """Timeseries

     Token/cost chart data plus the budget guards (`max_cost_usd`, per-turn loop budgets).

    Args:
        days (int | Unset):  Default: 7.
        bucket (None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UsageSeries]
    """

    kwargs = _get_kwargs(
        days=days,
        bucket=bucket,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 7,
    bucket: None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> HTTPValidationError | UsageSeries | None:
    """Timeseries

     Token/cost chart data plus the budget guards (`max_cost_usd`, per-turn loop budgets).

    Args:
        days (int | Unset):  Default: 7.
        bucket (None | TimeseriesApiUsageTimeseriesGetBucketType0 | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UsageSeries
    """

    return (
        await asyncio_detailed(
            client=client,
            days=days,
            bucket=bucket,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
