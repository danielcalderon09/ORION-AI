"""Director AI - Creative decision making for video editing."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.core.domain.entities.video_project import DirectorMemory, TemporalRange


@dataclass
class DirectorConfig:
    target_platform: str = "tiktok"
    min_clip_duration: float = 5.0
    max_clip_duration: float = 60.0
    preferred_clip_count: int = 3
    style: str = "auto"


class DirectorAgent(IAgent):
    """Agent responsible for creative editorial decisions."""

    def __init__(self, config: DirectorConfig | None = None):
        self.config = config or DirectorConfig()

    @property
    def agent_id(self) -> str:
        return "director_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return [
            "creative_brief_generation",
            "clip_selection",
            "pacing_decision",
            "style_matching",
        ]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        attention = context.get("attention_features", {})
        narrative = context.get("narrative_features", {})
        speech = context.get("speech_features", {})

        # Build creative brief
        brief = self._generate_creative_brief(context)

        # Select clips based on attention peaks and narrative climax
        clips = self._select_clips(attention, narrative)

        # Generate edit decisions
        decisions = self._build_edit_decisions(clips, speech)

        # Build debug timeline data if requested
        debug_timeline = None
        if context.get("debug_mode", False):
            debug_timeline = self._build_debug_timeline(
                attention, narrative, speech, clips, decisions
            )

        features = {
            "creative_brief": brief,
            "selected_clips": clips,
            "edit_decisions": decisions,
            "style_profile": self.config.style,
            "debug_timeline": debug_timeline,
        }

        duration = context.get("vision_features", {}).get("duration_seconds", 0)
        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )

    def _build_debug_timeline(self, attention, narrative, speech, clips, decisions) -> dict:
        """Build a comprehensive debug timeline for visualization."""
        timeline_points = []
        att_timeline = attention.get("timeline", [])

        for point in att_timeline:
            t = point.get("time", 0)
            entry = {
                "time": round(t, 2),
                "attention_score": round(point.get("attention_score", 0), 3),
                "audio_energy": round(point.get("audio_energy", 0), 3),
                "scene_change": round(point.get("scene_change", 0), 3),
                "speech_active": round(point.get("speech_active", 0), 3),
                "is_peak": any(abs(t - p["time"]) < 0.5 for p in attention.get("peaks", [])),
                "is_valley": any(abs(t - v["time"]) < 0.5 for v in attention.get("valleys", [])),
                "events": [],
            }

            # Add scene change events
            for sc in narrative.get("narrative_structure", {}).get("acts", []):
                if abs(t - sc.get("start", -1)) < 0.5:
                    entry["events"].append({"type": "act_start", "label": sc.get("name", "")})

            # Add climax markers
            for cl in narrative.get("climax_candidates", []):
                if abs(t - cl.get("timestamp", -1)) < 0.5:
                    entry["events"].append({"type": "climax_candidate", "score": round(cl.get("score", 0), 2)})

            # Add speech segments
            for seg in speech.get("segments", []):
                if seg["start"] <= t <= seg["end"]:
                    entry["events"].append({"type": "speech", "text": seg.get("text", "")[:50]})

            timeline_points.append(entry)

        # Clip markers
        clip_markers = []
        for clip in clips:
            clip_markers.append({
                "start": clip["start"],
                "end": clip["end"],
                "score": clip["score"],
                "source": clip["source"],
                "confidence_composite": clip.get("confidence", {}).get("composite", 0),
                "confidence_factors": clip.get("confidence_factors", {}),
            })

        return {
            "points": timeline_points,
            "clips": clip_markers,
            "summary": {
                "avg_attention": sum(p["attention_score"] for p in att_timeline) / len(att_timeline) if att_timeline else 0,
                "peak_count": len(attention.get("peaks", [])),
                "valley_count": len(attention.get("valleys", [])),
                "scene_count": narrative.get("narrative_structure", {}).get("scene_count", 0),
                "speech_segments": len(speech.get("segments", [])),
            },
        }

    def _generate_creative_brief(self, context: dict) -> dict:
        narrative = context.get("narrative_features", {})
        structure = narrative.get("narrative_structure", {})
        scenes = structure.get("scene_count", 0)

        return {
            "genre": "general",
            "platform": self.config.target_platform,
            "target_duration": "auto",
            "style_profile": self.config.style,
            "pacing": "dynamic" if scenes > 10 else "moderate",
            "audio_treatment": "enhance_peaks",
            "subtitle_style": "minimal",
        }

    def _select_clips(self, attention: dict, narrative: dict) -> list[dict]:
        peaks = attention.get("peaks", [])
        climax = narrative.get("climax_candidates", [])
        timeline = attention.get("timeline", [])

        # Merge and rank candidates with confidence scoring
        candidates = []
        for p in peaks:
            conf = self._compute_clip_confidence(
                timestamp=p["time"],
                attention_score=p["attention_score"],
                in_climax=any(abs(p["time"] - c["timestamp"]) < 3.0 for c in climax),
                narrative_context=narrative,
                timeline=timeline,
            )
            candidates.append({
                "timestamp": p["time"],
                "score": p["attention_score"],
                "source": "attention",
                "confidence": conf,
                "confidence_factors": conf.get("factors", {}),
            })
        for c in climax:
            conf = self._compute_clip_confidence(
                timestamp=c["timestamp"],
                attention_score=c["score"],
                in_climax=True,
                narrative_context=narrative,
                timeline=timeline,
            )
            candidates.append({
                "timestamp": c["timestamp"],
                "score": c["score"],
                "source": "narrative",
                "confidence": conf,
                "confidence_factors": conf.get("factors", {}),
            })

        # Sort by confidence composite score
        candidates.sort(key=lambda x: x["confidence"]["composite"], reverse=True)

        # Select top N with minimum spacing
        selected = []
        used_times = []
        for c in candidates:
            t = c["timestamp"]
            # Check spacing (avoid clips too close)
            too_close = any(abs(t - u) < self.config.min_clip_duration for u in used_times)
            if not too_close:
                clip = {
                    "clip_id": str(uuid4()),
                    "start": max(0, t - 2.0),
                    "end": t + min(self.config.max_clip_duration, 15.0),
                    "score": c["score"],
                    "source": c["source"],
                    "confidence": c["confidence"],
                    "confidence_factors": c["confidence_factors"],
                }
                selected.append(clip)
                used_times.append(t)

            if len(selected) >= self.config.preferred_clip_count:
                break

        return selected

    def _compute_clip_confidence(self, timestamp: float, attention_score: float, in_climax: bool,
                                  narrative_context: dict, timeline: list) -> dict:
        """Compute a detailed confidence score for a clip selection."""
        factors = {
            "attention_score": min(attention_score, 1.0),
            "in_climax_zone": 1.0 if in_climax else 0.3,
            "scene_density": 0.5,  # default
            "temporal_spread": 0.5,  # default
        }

        # Scene density factor
        structure = narrative_context.get("narrative_structure", {})
        scenes = structure.get("scene_count", 0)
        if scenes > 0:
            factors["scene_density"] = min(scenes / 20.0, 1.0)

        # Temporal spread (prefer clips not too close to start/end)
        duration = structure.get("duration", 0)
        if duration > 0:
            relative_pos = timestamp / duration
            # Peak confidence at center (0.4 - 0.8), lower at extremes
            factors["temporal_spread"] = 1.0 - abs(relative_pos - 0.6) * 1.5
            factors["temporal_spread"] = max(0.2, min(1.0, factors["temporal_spread"]))

        # Compute composite
        weights = {
            "attention_score": 0.35,
            "in_climax_zone": 0.30,
            "scene_density": 0.20,
            "temporal_spread": 0.15,
        }
        composite = sum(factors[k] * weights[k] for k in weights)

        return {
            "composite": round(composite, 3),
            "factors": {k: round(v, 3) for k, v in factors.items()},
            "weights": weights,
        }

    def _build_edit_decisions(self, clips: list, speech: dict) -> list[dict]:
        decisions = []
        segments = {s["start"]: s for s in speech.get("segments", [])}

        for clip in clips:
            # Find subtitle overlay window
            subtitles = []
            for seg in speech.get("segments", []):
                if clip["start"] <= seg["start"] <= clip["end"]:
                    subtitles.append(seg)

            decision = {
                "clip_id": clip["clip_id"],
                "temporal_range": (clip["start"], clip["end"]),
                "pacing": "fast" if clip["score"] > 0.8 else "normal",
                "subtitle_segments": subtitles,
                "audio_enhance": True,
            }
            decisions.append(decision)

        return decisions
