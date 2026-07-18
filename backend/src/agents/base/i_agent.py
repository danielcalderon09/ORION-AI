"""Base agent interface and types."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol


class AgentCapability(Enum):
    PERCEPTION = auto()
    COGNITION = auto()
    PRODUCTION = auto()
    LEARNING = auto()


@dataclass(frozen=True)
class AgentInput:
    """Immutable input to any agent."""

    media_reference: str  # Path or URI to media resource
    temporal_range: tuple[float, float] | None = None
    context: dict[str, Any] = None
    priority: int = 1

    def __post_init__(self):
        if self.context is None:
            object.__setattr__(self, "context", {})


@dataclass(frozen=True)
class AgentResult:
    """Immutable output from any agent."""

    agent_id: str
    agent_version: str
    capability: AgentCapability
    temporal_range: tuple[float, float]
    features: dict[str, Any]
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


class IAgent(Protocol):
    """Base protocol for all Orion agents."""

    @property
    def agent_id(self) -> str: ...

    @property
    def capability(self) -> AgentCapability: ...

    async def execute(self, input_data: AgentInput) -> AgentResult: ...

    def get_capabilities(self) -> list[str]: ...
