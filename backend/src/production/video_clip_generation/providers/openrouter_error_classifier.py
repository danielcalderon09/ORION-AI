"""Operation-aware HTTP error classification for OpenRouter video."""

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoAuthenticationError,
    OpenRouterVideoError,
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
    error: OpenRouterVideoError
    if status == 400 or status in {404, 409, 422}:
        error = OpenRouterVideoInvalidRequestError(message)
    elif status == 401:
        error = OpenRouterVideoAuthenticationError(message)
    elif status == 402:
        error = OpenRouterVideoInsufficientCreditsError(message)
    elif status == 403:
        error = OpenRouterVideoPermissionError(message)
    elif status == 429:
        error = OpenRouterVideoRateLimitError(message)
    elif status >= 500:
        error = OpenRouterVideoServerError(message)
    else:
        error = OpenRouterVideoTransportError(message)
    error.http_status = status
    raise error
