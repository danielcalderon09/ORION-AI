"""Attention Agent - Estimates viewer attention over time."""

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent


class IAttentionModel(Protocol):
    """Provider for attention estimation."""
    async def estimate_attention(self, features: dict[str, Any]) -> list[dict]: ...


@dataclass
class AttentionAgentConfig:
    audio_weight: float = 0.4
    visual_weight: float = 0.3
    motion_weight: float = 0.2
    speech_weight: float = 0.1
    smoothing_window: int = 5


class HeuristicAttentionProvider:
    """Attention estimation using audio energy, scene changes, and motion."""

    async def estimate_attention(self, features: dict[str, Any]) -> list[dict]:
        audio_features = features.get("audio_features", {})
        vision_features = features.get("vision_features", {})
        speech_features = features.get("speech_features", {})

        # Audio energy timeline
        rms = audio_features.get("rms_energy", {})
        rms_times = rms.get("times", [])
        rms_values = rms.get("values", [])

        # Scene changes
        scenes = vision_features.get("scene_changes", [])
        scene_timestamps = {s["timestamp"]: s["score"] for s in scenes}

        # Speech segments
        segments = speech_features.get("segments", [])
        speech_timestamps = {}
        for seg in segments:
            t = seg.get("start", 0)
            speech_timestamps[t] = 1.0

        # Combine into attention timeline
        timeline = []
        for i, t in enumerate(rms_times):
            if i >= len(rms_values):
                break

            audio_score = min(rms_values[i] * 2, 1.0) if rms_values else 0

            scene_score = 0
            for st, ss in scene_timestamps.items():
                if abs(st - t) < 0.5:
                    scene_score = max(scene_score, min(ss * 3, 1.0))

            speech_score = 0
            for st, ss in speech_timestamps.items():
                if abs(st - t) < 1.0:
                    speech_score = max(speech_score, ss)

            attention = (
                0.4 * audio_score +
                0.3 * scene_score +
                0.2 * (1.0 if scene_score > 0 else 0) +
                0.1 * speech_score
            )

            timeline.append({
                "time": float(t),
                "attention_score": float(min(attention, 1.0)),
                "audio_energy": float(audio_score),
                "scene_change": float(scene_score),
                "speech_active": float(speech_score),
            })

        # Smooth
        if len(timeline) > 5:
            scores = [p["attention_score"] for p in timeline]
            smoothed = np.convolve(scores, np.ones(5)/5, mode="same")
            for i, point in enumerate(timeline):
                point["attention_score"] = float(smoothed[i])

        return timeline


class AttentionAgent(IAgent):
    """Agent responsible for attention curve estimation."""

    def __init__(
        self,
        attention_provider: IAttentionModel | None = None,
        config: AttentionAgentConfig | None = None,
    ):
        self.attention_provider = attention_provider or HeuristicAttentionProvider()
        self.config = config or AttentionAgentConfig()

    @property
    def agent_id(self) -> str:
        return "attention_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return ["attention_estimation", "engagement_prediction", "retention_analysis"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}

        merged = {
            "audio_features": context.get("audio_features", {}),
            "vision_features": context.get("vision_features", {}),
            "speech_features": context.get("speech_features", {}),
        }

        timeline = await self.attention_provider.estimate_attention(merged)

        # Find peaks and valleys
        peaks = []
        valleys = []
        for i, point in enumerate(timeline):
            score = point["attention_score"]
            if score > 0.7:
                peaks.append(point)
            elif score < 0.2:
                valleys.append(point)

        duration = context.get("vision_features", {}).get("duration_seconds", 0)
        temporal_range = input_data.temporal_range or (0.0, duration)

        features = {
            "timeline": timeline,
            "peaks": peaks,
            "valleys": valleys,
            "avg_attention": sum(p["attention_score"] for p in timeline) / len(timeline) if timeline else 0,
        }

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
