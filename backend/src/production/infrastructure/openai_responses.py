"""Deprecated aliases for the neutral OpenAI-compatible transport."""

from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError as OpenAIResponsesAuthenticationError,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleError as OpenAIResponsesError,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleProtocolError as OpenAIResponsesProtocolError,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleRateLimitError as OpenAIResponsesRateLimitError,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleResponsesClient as OpenAIResponsesClient,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleTimeoutError as OpenAIResponsesTimeoutError,
)
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleUnavailableError as OpenAIResponsesUnavailableError,
)
from backend.src.production.infrastructure.openai_compatible import Sleeper

__all__ = [
    "OpenAIResponsesAuthenticationError",
    "OpenAIResponsesClient",
    "OpenAIResponsesError",
    "OpenAIResponsesProtocolError",
    "OpenAIResponsesRateLimitError",
    "OpenAIResponsesTimeoutError",
    "OpenAIResponsesUnavailableError",
    "Sleeper",
]
