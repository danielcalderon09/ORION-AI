"""Narrative Intelligence Agent - Reconstructs story structure from video."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent


class INarrativeModel(Protocol):
    """Provider for narrative understanding."""
    async def analyze_structure(self, features: dict[str, Any]) -> dict[str, Any]: ...
    async def detect_micro_stories(self, timeline: list[dict]) -> list[dict]: ...
    async def identify_climax(self, narrative: dict, attention: list[dict]) -> list[dict]: ...


@dataclass
class NarrativeAgentConfig:
    min_scene_duration: float = 2.0
    climax_attention_threshold: float = 0.7


class HeuristicNarrativeProvider:
    """Heuristic narrative analysis using scene changes and audio peaks."""

    async def analyze_structure(self, features: dict[str, Any]) -> dict[str, Any]:
        scenes = features.get("scene_changes", [])
        duration = features.get("duration_seconds", 0)

        if not scenes or duration == 0:
            return {
                "acts": [],
                "beats": [],
                "climax_candidates": [],
            }

        # Simple 3-act structure based on time
        intro_end = duration * 0.15
        climax_zone_start = duration * 0.6
        climax_zone_end = duration * 0.85

        acts = [
            {"name": "introduction", "start": 0, "end": intro_end},
            {"name": "development", "start": intro_end, "end": climax_zone_start},
            {"name": "climax_zone", "start": climax_zone_start, "end": climax_zone_end},
            {"name": "resolution", "start": climax_zone_end, "end": duration},
        ]

        # Beats from scene changes
        beats = []
        for scene in scenes:
            beat = {
                "timestamp": scene.get("timestamp", 0),
                "type": "scene_change",
                "intensity": scene.get("score", 0),
            }
            beats.append(beat)

        return {
            "acts": acts,
            "beats": beats,
            "scene_count": len(scenes),
            "duration": duration,
        }

    async def detect_micro_stories(self, timeline: list[dict]) -> list[dict]:
        """Detect micro-stories from attention peaks."""
        if not timeline:
            return []

        micro_stories = []
        current_start = timeline[0].get("time", 0)

        for i, point in enumerate(timeline[1:], 1):
            prev = timeline[i - 1]
            # Gap indicates new micro-story
            if point.get("time", 0) - prev.get("time", 0) > 3.0:
                micro_stories.append({
                    "start": current_start,
                    "end": prev.get("time", 0),
                    "type": "micro_story",
                })
                current_start = point.get("time", 0)

        # Close last
        if timeline:
            micro_stories.append({
                "start": current_start,
                "end": timeline[-1].get("time", 0),
                "type": "micro_story",
            })

        return micro_stories

    async def identify_climax(self, narrative: dict, attention: list[dict]) -> list[dict]:
        """Identify climax moments from attention + narrative zone."""
        climax_zone = next(
            (a for a in narrative.get("acts", []) if a["name"] == "climax_zone"), None
        )
        if not climax_zone:
            return []

        candidates = []
        for point in attention:
            t = point.get("time", 0)
            if climax_zone["start"] <= t <= climax_zone["end"]:
                score = point.get("attention_score", 0)
                if score > 0.5:
                    candidates.append({
                        "timestamp": t,
                        "score": score,
                        "source": "attention_in_climax_zone",
                    })

        # Sort by score
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:5]


class NarrativeIntelligenceAgent(IAgent):
    """Agent responsible for narrative understanding."""

    def __init__(
        self,
        narrative_provider: INarrativeModel | None = None,
        config: NarrativeAgentConfig | None = None,
    ):
        self.narrative_provider = narrative_provider or HeuristicNarrativeProvider()
        self.config = config or NarrativeAgentConfig()

    @property
    def agent_id(self) -> str:
        return "narrative_intelligence_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return ["narrative_structure", "micro_story_detection", "climax_identification"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        vision_features = context.get("vision_features", {})
        audio_features = context.get("audio_features", {})
        attention_features = context.get("attention_features", {})

        # Merge features for narrative analysis
        merged = {
            **vision_features,
            **audio_features,
            "attention_peaks": attention_features.get("peaks", []),
        }

        structure = await self.narrative_provider.analyze_structure(merged)

        attention_timeline = attention_features.get("timeline", [])
        micro_stories = await self.narrative_provider.detect_micro_stories(attention_timeline)
        climax = await self.narrative_provider.identify_climax(structure, attention_timeline)

        features = {
            "narrative_structure": structure,
            "micro_stories": micro_stories,
            "climax_candidates": climax,
        }

        duration = vision_features.get("duration_seconds", 0)
        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
