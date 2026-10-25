from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_transcribe_api_voice_transcribe_post import BodyTranscribeApiVoiceTranscribePost
from ...models.voice_transcription import VoiceTranscription
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: BodyTranscribeApiVoiceTranscribePost,
    language: None | str | Unset = UNSET,
    session_id: None | str | Unset = UNSET,
    translate_to: None | str | Unset = UNSET,
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

    json_language: None | str | Unset
    if isinstance(language, Unset):
        json_language = UNSET
    else:
        json_language = language
    params["language"] = json_language

    json_session_id: None | str | Unset
    if isinstance(session_id, Unset):
        json_session_id = UNSET
    else:
        json_session_id = session_id
    params["session_id"] = json_session_id

    json_translate_to: None | str | Unset
    if isinstance(translate_to, Unset):
        json_translate_to = UNSET
    else:
        json_translate_to = translate_to
    params["translate_to"] = json_translate_to

    json_token: None | str | Unset
    if isinstance(token, Unset):
        json_token = UNSET
    else:
        json_token = token
    params["token"] = json_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/voice/transcribe",
        "params": params,
        "cookies": cookies,
    }

    _kwargs["files"] = body.to_multipart()

    headers["Content-Type"] = "multipart/form-data; boundary=+++"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | VoiceTranscription | None:
    if response.status_code == 200:
        response_200 = VoiceTranscription.from_dict(response.json())

        return response_200

    if response.status_code == 413:
        response_413 = cast(Any, None)
        return response_413

    if response.status_code == 422:
        response_422 = cast(Any, None)
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
) -> Response[Any | VoiceTranscription]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeApiVoiceTranscribePost,
    language: None | str | Unset = UNSET,
    session_id: None | str | Unset = UNSET,
    translate_to: None | str | Unset = UNSET,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | VoiceTranscription]:
    """Transcribe

     Normalize → VAD gate → STT. With `session_id` the text is also submitted as a followup;
    with `translate_to` the transcript is also translated (`translation`).

    Args:
        language (None | str | Unset):
        session_id (None | str | Unset):
        translate_to (None | str | Unset):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (BodyTranscribeApiVoiceTranscribePost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | VoiceTranscription]
    """

    kwargs = _get_kwargs(
        body=body,
        language=language,
        session_id=session_id,
        translate_to=translate_to,
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
    body: BodyTranscribeApiVoiceTranscribePost,
    language: None | str | Unset = UNSET,
    session_id: None | str | Unset = UNSET,
    translate_to: None | str | Unset = UNSET,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | VoiceTranscription | None:
    """Transcribe

     Normalize → VAD gate → STT. With `session_id` the text is also submitted as a followup;
    with `translate_to` the transcript is also translated (`translation`).

    Args:
        language (None | str | Unset):
        session_id (None | str | Unset):
        translate_to (None | str | Unset):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (BodyTranscribeApiVoiceTranscribePost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | VoiceTranscription
    """

    return sync_detailed(
        client=client,
        body=body,
        language=language,
        session_id=session_id,
        translate_to=translate_to,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeApiVoiceTranscribePost,
    language: None | str | Unset = UNSET,
    session_id: None | str | Unset = UNSET,
    translate_to: None | str | Unset = UNSET,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Response[Any | VoiceTranscription]:
    """Transcribe

     Normalize → VAD gate → STT. With `session_id` the text is also submitted as a followup;
    with `translate_to` the transcript is also translated (`translation`).

    Args:
        language (None | str | Unset):
        session_id (None | str | Unset):
        translate_to (None | str | Unset):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (BodyTranscribeApiVoiceTranscribePost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | VoiceTranscription]
    """

    kwargs = _get_kwargs(
        body=body,
        language=language,
        session_id=session_id,
        translate_to=translate_to,
        token=token,
        authorization=authorization,
        auoreo_token=auoreo_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeApiVoiceTranscribePost,
    language: None | str | Unset = UNSET,
    session_id: None | str | Unset = UNSET,
    translate_to: None | str | Unset = UNSET,
    token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    auoreo_token: None | str | Unset = UNSET,
) -> Any | VoiceTranscription | None:
    """Transcribe

     Normalize → VAD gate → STT. With `session_id` the text is also submitted as a followup;
    with `translate_to` the transcript is also translated (`translation`).

    Args:
        language (None | str | Unset):
        session_id (None | str | Unset):
        translate_to (None | str | Unset):
        token (None | str | Unset):
        authorization (None | str | Unset):
        auoreo_token (None | str | Unset):
        body (BodyTranscribeApiVoiceTranscribePost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | VoiceTranscription
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            language=language,
            session_id=session_id,
            translate_to=translate_to,
            token=token,
            authorization=authorization,
            auoreo_token=auoreo_token,
        )
    ).parsed
