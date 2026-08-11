"""Neutral transport for OpenAI-compatible Chat Completions APIs."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

Sleeper = Callable[[float], Awaitable[None]]


class OpenAICompatibleError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class OpenAICompatibleAuthenticationError(OpenAICompatibleError):
    pass


class OpenAICompatibleRateLimitError(OpenAICompatibleError):
    pass


class OpenAICompatibleTimeoutError(OpenAICompatibleError):
    pass


class OpenAICompatibleUnavailableError(OpenAICompatibleError):
    pass


class OpenAICompatibleProtocolErrorCode(StrEnum):
    RESPONSE_TOO_LARGE = "response_too_large"
    RESPONSE_JSON = "response_json"
    RESPONSE_ENVELOPE = "response_envelope"
    OUTPUT_TEXT = "output_text"
    HTTP_STATUS = "http_status"


class OpenAICompatibleProtocolError(OpenAICompatibleError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: OpenAICompatibleProtocolErrorCode,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class OpenAICompatibleResponse:
    body: dict[str, Any]
    request_id: str | None
    http_status: int


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _validate_header_value(name: str, value: str, *, maximum: int) -> str:
    stripped = value.strip()
    if not stripped or len(stripped) > maximum or any(ord(char) < 32 for char in stripped):
        raise ValueError(f"{name} is invalid")
    return stripped


def load_strict_json_object(value: str) -> dict[str, Any]:
    """Decode one JSON object while rejecting duplicates and non-standard numbers."""

    result = json.loads(
        value,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(result, dict):
        raise ValueError("JSON value must be an object")
    return result


class OpenAICompatibleResponsesClient:
    """Own retry, status, strict JSON, headers, and lifecycle mechanics."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_transport_attempts: int,
        retry_base_delay_seconds: float,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        owns_client: bool = False,
        max_response_bytes: int = 2_000_000,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme != "https" or not parsed_url.host or parsed_url.userinfo:
            raise ValueError("provider base URL must be HTTPS and contain no credentials")
        if not api_key.strip():
            raise ValueError("provider credential is missing")
        if not 1 <= max_response_bytes <= 20_000_000:
            raise ValueError("provider response size limit is outside safe bounds")
        self._attempts = max_transport_attempts
        self._retry_delay = retry_base_delay_seconds
        self._sleeper = sleeper
        self._client = client
        self._owns_client = client is None or owns_client
        self._closed = False
        self._max_response_bytes = max_response_bytes
        self._base_url = str(parsed_url).rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if http_referer is not None:
            referer = _validate_header_value("HTTP-Referer", http_referer, maximum=2048)
            parsed_referer = httpx.URL(referer)
            if (
                parsed_referer.scheme not in {"http", "https"}
                or not parsed_referer.host
                or parsed_referer.userinfo
            ):
                raise ValueError("HTTP-Referer is invalid")
            self._headers["HTTP-Referer"] = referer
        if app_title is not None:
            self._headers["X-Title"] = _validate_header_value("X-Title", app_title, maximum=200)

    async def post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        response = await self.post_with_metadata(payload)
        return response.body, response.request_id

    async def post_with_metadata(
        self,
        payload: dict[str, Any],
    ) -> OpenAICompatibleResponse:
        if self._closed:
            raise OpenAICompatibleError("provider transport is closed")
        last_error: OpenAICompatibleError | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                async with self._get_client().stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._headers,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    header_request_id = self.safe_string(response.headers.get("x-request-id"))
                    self._raise_for_status(response.status_code, header_request_id)
                    try:
                        content = await _read_bounded_response(
                            response,
                            maximum=self._max_response_bytes,
                        )
                        body = json.loads(
                            content.decode("utf-8", errors="strict"),
                            parse_constant=_reject_constant,
                            parse_float=Decimal,
                            object_pairs_hook=_reject_duplicate_keys,
                        )
                    except OpenAICompatibleProtocolError as exc:
                        raise OpenAICompatibleProtocolError(
                            str(exc),
                            diagnostic_code=exc.diagnostic_code,
                            status_code=response.status_code,
                            request_id=header_request_id,
                        ) from exc
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        raise OpenAICompatibleProtocolError(
                            "provider returned invalid JSON",
                            diagnostic_code=OpenAICompatibleProtocolErrorCode.RESPONSE_JSON,
                            status_code=response.status_code,
                            request_id=header_request_id,
                        ) from exc
                    if not isinstance(body, dict):
                        raise OpenAICompatibleProtocolError(
                            "provider response must be an object",
                            diagnostic_code=(
                                OpenAICompatibleProtocolErrorCode.RESPONSE_ENVELOPE
                            ),
                            status_code=response.status_code,
                            request_id=header_request_id,
                        )
                    request_id = header_request_id or self.safe_string(body.get("id"))
                    return OpenAICompatibleResponse(
                        body=body,
                        request_id=request_id,
                        http_status=response.status_code,
                    )
            except asyncio.CancelledError:
                raise
            except (
                OpenAICompatibleTimeoutError,
                OpenAICompatibleRateLimitError,
                OpenAICompatibleUnavailableError,
            ) as exc:
                last_error = exc
            except httpx.TimeoutException as exc:
                last_error = OpenAICompatibleTimeoutError("provider request timed out")
                last_error.__cause__ = exc
            except httpx.RequestError as exc:
                last_error = OpenAICompatibleUnavailableError("provider connection failed")
                last_error.__cause__ = exc
            if attempt < self._attempts:
                await self._sleeper(self._retry_delay * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _raise_for_status(status: int, request_id: str | None = None) -> None:
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise OpenAICompatibleAuthenticationError(
                "provider rejected authentication",
                status_code=status,
                request_id=request_id,
            )
        if status == 429:
            raise OpenAICompatibleRateLimitError(
                "provider rate limit reached",
                status_code=status,
                request_id=request_id,
            )
        if status in {408, 425} or status >= 500:
            raise OpenAICompatibleUnavailableError(
                "provider is unavailable",
                status_code=status,
                request_id=request_id,
            )
        raise OpenAICompatibleProtocolError(
            f"provider returned unsupported status {status}",
            diagnostic_code=OpenAICompatibleProtocolErrorCode.HTTP_STATUS,
            status_code=status,
            request_id=request_id,
        )

    @staticmethod
    def extract_single_output_text(body: dict[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise OpenAICompatibleProtocolError(
                "response must contain exactly one choice",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT,
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise OpenAICompatibleProtocolError(
                "provider choice is invalid",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT,
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise OpenAICompatibleProtocolError(
                "provider message content is missing",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT,
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise OpenAICompatibleProtocolError(
                "provider message content is missing",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT,
            )
        if not content.strip():
            raise OpenAICompatibleProtocolError(
                "provider message content is empty",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT,
            )
        return content

    @staticmethod
    def extract_finish_reason(body: dict[str, Any]) -> str | None:
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            return None
        choice = choices[0]
        return OpenAICompatibleResponsesClient.safe_string(
            choice.get("finish_reason") if isinstance(choice, dict) else None
        )

    @staticmethod
    def safe_int(value: Any) -> int | None:
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    @staticmethod
    def safe_string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise OpenAICompatibleError("provider transport is closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
            )
        return self._client


async def _read_bounded_response(
    response: httpx.Response,
    *,
    maximum: int,
) -> bytes:
    result = bytearray()
    async for chunk in response.aiter_bytes():
        result.extend(chunk)
        if len(result) > maximum:
            raise OpenAICompatibleProtocolError(
                "provider response exceeded the safe size limit",
                diagnostic_code=OpenAICompatibleProtocolErrorCode.RESPONSE_TOO_LARGE,
            )
    return bytes(result)
