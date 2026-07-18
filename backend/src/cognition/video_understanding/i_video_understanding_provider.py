"""Video Understanding Provider interface (preparation for Sprint 2)."""

from typing import Any, Protocol


class IVideoUnderstandingProvider(Protocol):
    """Provider for multimodal video understanding.
    
    This interface abstracts the concrete model (Qwen2-VL, LLaVA, etc.)
    so the system depends on capabilities, not implementations.
    """

    async def describe_scene(self, frame_embedding: list[float], audio_context: dict, text_context: str) -> dict[str, Any]: ...
    async def identify_characters(self, face_embeddings: list[list[float]]) -> list[dict[str, Any]]: ...
    async def understand_action(self, frame_sequence: list[Any], audio_events: list[dict]) -> list[dict[str, Any]]: ...
    async def generate_scene_caption(self, frame: Any, prev_caption: str | None = None) -> str: ...
    async def classify_genre(self, features: dict[str, Any]) -> str: ...


class DummyVideoUnderstandingProvider:
    """Placeholder provider for Sprint 1.5 (heuristic fallback)."""

    async def describe_scene(self, frame_embedding, audio_context, text_context):
        return {"description": "Heuristic scene (no multimodal model)", "confidence": 0.5}

    async def identify_characters(self, face_embeddings):
        return []

    async def understand_action(self, frame_sequence, audio_events):
        return []

    async def generate_scene_caption(self, frame, prev_caption=None):
        return "Scene description unavailable"

    async def classify_genre(self, features):
        # Simple heuristic genre classification
        scene_count = features.get("scene_count", 0)
        audio_peaks = len(features.get("audio_peaks", []))
        if audio_peaks > 20 and scene_count > 15:
            return "gaming"
        elif audio_peaks < 5 and scene_count < 5:
            return "podcast"
        elif scene_count > 30:
            return "action"
        return "general"
