"""Capability and Model Registry for provider resolution."""

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ModelMetadata:
    """Metadata for a registered model/provider."""

    model_id: str
    capability: str
    provider_class: type
    version: str
    description: str
    requirements: list[str]
    default: bool = False


class CapabilityRegistry:
    """Registry that resolves capabilities to concrete providers."""

    def __init__(self):
        self._providers: dict[str, list[ModelMetadata]] = {}
        self._instances: dict[str, Any] = {}

    def register(self, metadata: ModelMetadata) -> None:
        """Register a provider for a capability."""
        if metadata.capability not in self._providers:
            self._providers[metadata.capability] = []
        self._providers[metadata.capability].append(metadata)

    def resolve(self, capability: str, model_id: str | None = None) -> type:
        """Resolve a capability to a provider class."""
        if capability not in self._providers:
            raise ValueError(f"No providers registered for capability: {capability}")

        candidates = self._providers[capability]
        if model_id:
            for meta in candidates:
                if meta.model_id == model_id:
                    return meta.provider_class
            raise ValueError(f"Model {model_id} not found for capability {capability}")

        # Return default or first available
        defaults = [m for m in candidates if m.default]
        if defaults:
            return defaults[0].provider_class
        return candidates[0].provider_class

    def get_instance(self, capability: str, model_id: str | None = None, **kwargs: Any) -> Any:
        """Get or create an instance of the resolved provider."""
        key = f"{capability}:{model_id or 'default'}"
        if key not in self._instances:
            provider_class = self.resolve(capability, model_id)
            self._instances[key] = provider_class(**kwargs)
        return self._instances[key]

    def list_capabilities(self) -> list[str]:
        return list(self._providers.keys())

    def list_providers(self, capability: str) -> list[ModelMetadata]:
        return self._providers.get(capability, [])

    def set_default(self, capability: str, model_id: str) -> None:
        """Mark a specific model as default for a capability."""
        if capability not in self._providers:
            raise ValueError(f"Capability not found: {capability}")
        for meta in self._providers[capability]:
            meta_dict = meta.__dict__ if hasattr(meta, "__dict__") else {}
            if meta.model_id == model_id:
                # Create new metadata with default=True
                new_meta = ModelMetadata(
                    model_id=meta.model_id,
                    capability=meta.capability,
                    provider_class=meta.provider_class,
                    version=meta.version,
                    description=meta.description,
                    requirements=meta.requirements,
                    default=True,
                )
                idx = self._providers[capability].index(meta)
                self._providers[capability][idx] = new_meta
            else:
                # Unset default for others
                new_meta = ModelMetadata(
                    model_id=meta.model_id,
                    capability=meta.capability,
                    provider_class=meta.provider_class,
                    version=meta.version,
                    description=meta.description,
                    requirements=meta.requirements,
                    default=False,
                )
                idx = self._providers[capability].index(meta)
                self._providers[capability][idx] = new_meta
