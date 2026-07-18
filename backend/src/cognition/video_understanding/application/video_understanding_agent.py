"""Video Understanding Agent — integrates multimodal semantic understanding."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.cognition.video_understanding.i_video_understanding_provider import (
    IVideoUnderstandingProvider,
)
from backend.src.learning.semantic_memory.domain.semantic_concept import (
    EmbeddingVector, SemanticConcept,
)
from backend.src.learning.semantic_memory.infrastructure.faiss_semantic_memory import (
    FaissSemanticMemory,
)


@dataclass
class VideoUnderstandingConfig:
    extract_scenes: bool = True
    extract_characters: bool = True
    extract_actions: bool = True
    generate_captions: bool = True
    classify_genre: bool = True
    store_in_semantic_memory: bool = True


class VideoUnderstandingAgent(IAgent):
    """Agent that provides semantic understanding of video content via multimodal providers."""

    def __init__(
        self,
        understanding_provider: IVideoUnderstandingProvider,
        semantic_memory: FaissSemanticMemory | None = None,
        config: VideoUnderstandingConfig | None = None,
    ):
        self.provider = understanding_provider
        self.semantic_memory = semantic_memory or FaissSemanticMemory()
        self.config = config or VideoUnderstandingConfig()

    @property
    def agent_id(self) -> str:
        return "video_understanding_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return [
            "scene_understanding",
            "character_recognition",
            "action_classification",
            "genre_classification",
            "semantic_embedding",
        ]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        vision_features = context.get("vision_features", {})
        audio_features = context.get("audio_features", {})
        speech_features = context.get("speech_features", {})
        video_path = Path(input_data.media_reference)

        # Get key frames for analysis
        key_frames = vision_features.get("key_frames", [])
        duration = vision_features.get("duration_seconds", 0)

        features: dict[str, Any] = {
            "genre": "unknown",
            "scenes": [],
            "characters": [],
            "actions": [],
            "embeddings": [],
        }

        # Genre classification
        if self.config.classify_genre:
            merged = {
                **vision_features,
                **audio_features,
                "audio_peaks": audio_features.get("peaks", []),
            }
            genre = await self.provider.classify_genre(merged)
            features["genre"] = genre

        # Process key frames if available
        if key_frames:
            for i, frame_data in enumerate(key_frames[:10]):  # Limit to first 10 for performance
                timestamp = frame_data.get("timestamp", i * duration / 10 if duration else i)

                # Scene description
                if self.config.extract_scenes:
                    desc = await self.provider.describe_scene(
                        frame_embedding=frame_data.get("embedding", []),
                        audio_context=audio_features,
                        text_context=speech_features.get("transcript", "")[:500],
                    )
                    scene_concept = SemanticConcept(
                        concept_id=f"scene_{input_data.media_reference}_{i}",
                        concept_type="scene",
                        label=desc.get("description", f"Scene at {timestamp:.1f}s"),
                        description=desc.get("description", ""),
                        embeddings=[EmbeddingVector(
                            vector_id=f"emb_scene_{i}",
                            concept_type="scene",
                            label=f"scene_{i}",
                            vector=frame_data.get("embedding", []),
                            timestamp=timestamp,
                        )],
                        related_concepts=[],
                        occurrences=[],
                        confidence=desc.get("confidence", 0.5),
                    )
                    if self.config.store_in_semantic_memory:
                        self.semantic_memory.store_concept(scene_concept)
                    features["scenes"].append({
                        "timestamp": timestamp,
                        "description": desc.get("description", ""),
                        "confidence": desc.get("confidence", 0.5),
                    })

                # Action understanding
                if self.config.extract_actions and i > 0:
                    prev_frame = key_frames[i - 1]
                    actions = await self.provider.understand_action(
                        frame_sequence=[prev_frame, frame_data],
                        audio_events=audio_features.get("peaks", []),
                    )
                    for action in actions:
                        features["actions"].append({
                            "timestamp": timestamp,
                            "action": action.get("action", "unknown"),
                            "confidence": action.get("confidence", 0.5),
                        })

        # Character identification (placeholder for when face embeddings available)
        if self.config.extract_characters:
            face_embeddings = vision_features.get("face_embeddings", [])
            if face_embeddings:
                characters = await self.provider.identify_characters(face_embeddings)
                for char in characters:
                    char_concept = SemanticConcept(
                        concept_id=f"char_{char.get('id', 'unknown')}",
                        concept_type="character",
                        label=char.get("name", "Unknown"),
                        description=f"Character detected in video",
                        embeddings=[EmbeddingVector(
                            vector_id=f"emb_char_{char.get('id', '0')}",
                            concept_type="character",
                            label=char.get("name", "Unknown"),
                            vector=char.get("embedding", []),
                        )],
                        related_concepts=[],
                        occurrences=[],
                        confidence=char.get("confidence", 0.5),
                    )
                    if self.config.store_in_semantic_memory:
                        self.semantic_memory.store_concept(char_concept)
                    features["characters"].append(char)

        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.2.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
