"""Operation-aware HTTP error classification for OpenRouter video."""

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoAuthenticationError,
    OpenRouterVideoInsufficientCreditsError,
    OpenRouterVideoInvalidRequestError,
    OpenRouterVideoPermissionError,
    OpenRouterVideoRateLimitError,
    OpenRouterVideoServerError,
    OpenRouterVideoTransportError,
)


def raise_for_openrouter_status(status: int, *, operation: str) -> None:
    if 200 <= status < 300:
        return
    message = f"OpenRouter video {operation} failed with HTTP {status}"
    if status == 400 or status in {404, 409, 422}:
        raise OpenRouterVideoInvalidRequestError(message)
    if status == 401:
        raise OpenRouterVideoAuthenticationError(message)
    if status == 402:
        raise OpenRouterVideoInsufficientCreditsError(message)
    if status == 403:
        raise OpenRouterVideoPermissionError(message)
    if status == 429:
        raise OpenRouterVideoRateLimitError(message)
    if status >= 500:
        raise OpenRouterVideoServerError(message)
    raise OpenRouterVideoTransportError(message)
