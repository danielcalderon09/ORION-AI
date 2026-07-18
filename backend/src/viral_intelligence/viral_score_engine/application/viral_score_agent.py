"""Viral Score Engine implementation with decomposed factors."""

import asyncio
from typing import Any

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.viral_intelligence.viral_score_engine.domain.viral_score import (
    ViralFactor, ViralScore, ViralScoreMap,
)


class HookFactorProvider:
    """Estimates hook strength of a segment."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        attention = features.get("attention_features", {})
        audio = features.get("audio_features", {})
        speech = features.get("speech_features", {})

        # Hook strength: first 3 seconds attention + audio onset + speech start
        peaks = attention.get("peaks", [])
        onset = audio.get("onset_strength", {}).get("values", [])
        segments = speech.get("segments", [])

        # Score based on whether there's an immediate peak or strong onset
        has_early_peak = any(p["time"] < 3.0 for p in peaks[:5])
        has_strong_onset = any(o > 0.3 for o in onset[:3]) if onset else False
        starts_with_speech = any(s["start"] < 1.0 for s in segments[:3])

        score = 0.3
        if has_early_peak:
            score += 0.35
        if has_strong_onset:
            score += 0.20
        if starts_with_speech:
            score += 0.15

        return ViralFactor(
            factor_name="hook",
            score=min(score, 1.0),
            weight=0.20,
            description="Strength of opening seconds in capturing attention",
            evidence=[
                {"early_peak": has_early_peak, "strong_onset": has_strong_onset, "speech_start": starts_with_speech}
            ],
        )


class EmotionFactorProvider:
    """Estimates emotional impact of a segment."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        attention = features.get("attention_features", {})
        audio = features.get("audio_features", {})

        # Emotion proxy: attention variance + audio energy variance
        timeline = attention.get("timeline", [])
        if timeline:
            scores = [p["attention_score"] for p in timeline]
            variance = max(scores) - min(scores) if len(scores) > 1 else 0
        else:
            variance = 0

        # Audio peaks indicate excitement/surprise
        peaks = audio.get("peaks", [])
        peak_density = len(peaks) / max(audio.get("duration", 60), 1)

        score = min(0.3 + variance * 0.5 + peak_density * 0.3, 1.0)

        return ViralFactor(
            factor_name="emotion",
            score=score,
            weight=0.15,
            description="Emotional dynamism and surprise potential",
            evidence=[{"attention_variance": round(variance, 3), "peak_density": round(peak_density, 3)}],
        )


class CuriosityFactorProvider:
    """Estimates curiosity generation (open loops, incomplete information)."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        speech = features.get("speech_features", {})
        narrative = features.get("narrative_features", {})

        # Curiosity from incomplete speech segments at boundaries
        segments = speech.get("segments", [])
        incomplete_at_end = False
        if segments and segments[-1]["end"] > segments[-1].get("expected_end", segments[-1]["end"]):
            incomplete_at_end = True

        # Narrative beats create curiosity
        beats = narrative.get("narrative_structure", {}).get("beats", [])
        beat_density = len(beats) / max(narrative.get("narrative_structure", {}).get("duration", 60), 1)

        score = min(0.4 + beat_density * 0.4 + (0.2 if incomplete_at_end else 0), 1.0)

        return ViralFactor(
            factor_name="curiosity",
            score=score,
            weight=0.15,
            description="Potential to generate curiosity and open loops",
            evidence=[{"beat_density": round(beat_density, 3), "incomplete_end": incomplete_at_end}],
        )


class VisualPacingFactorProvider:
    """Estimates visual pacing dynamism."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        vision = features.get("vision_features", {})
        scenes = vision.get("scene_changes", [])
        duration = vision.get("video_info", {}).get("duration_seconds", 60)

        scene_rate = len(scenes) / max(duration, 1)
        # Optimal: 0.3-1.0 scene changes per second (fast but not chaotic)
        if scene_rate < 0.1:
            score = 0.2
        elif scene_rate < 0.3:
            score = 0.5
        elif scene_rate < 1.0:
            score = 0.9
        else:
            score = 0.7  # too chaotic

        return ViralFactor(
            factor_name="visual_pacing",
            score=score,
            weight=0.10,
            description="Dynamism of visual changes and cuts",
            evidence=[{"scene_rate": round(scene_rate, 3)}],
        )


class SpeechPacingFactorProvider:
    """Estimates speech pacing engagement."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        speech = features.get("speech_features", {})
        segments = speech.get("segments", [])

        if not segments:
            return ViralFactor(
                factor_name="speech_pacing",
                score=0.3,
                weight=0.10,
                description="No speech detected",
                evidence=[],
            )

        # Ideal: 2-4 words per second (fast but intelligible)
        total_words = sum(len(s.get("text", "").split()) for s in segments)
        total_speech_time = sum(s["end"] - s["start"] for s in segments)
        wps = total_words / max(total_speech_time, 1)

        if wps < 1.5:
            score = 0.4
        elif wps < 2.5:
            score = 0.7
        elif wps < 4.0:
            score = 0.95
        else:
            score = 0.8  # too fast

        return ViralFactor(
            factor_name="speech_pacing",
            score=score,
            weight=0.10,
            description="Pace and rhythm of speech delivery",
            evidence=[{"words_per_second": round(wps, 2)}],
        )


class NoveltyFactorProvider:
    """Estimates novelty relative to semantic memory."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        # For Sprint 3: simple heuristic based on scene uniqueness
        vision = features.get("vision_features", {})
        scenes = vision.get("scene_changes", [])

        # More scene changes = more novelty (within reason)
        duration = vision.get("video_info", {}).get("duration_seconds", 60)
        scene_rate = len(scenes) / max(duration, 1)

        score = min(0.3 + scene_rate * 0.5, 1.0)
        if scene_rate > 1.5:
            score = 0.6  # penalty for excessive chaos

        return ViralFactor(
            factor_name="novelty",
            score=score,
            weight=0.10,
            description="Uniqueness and surprise factor of content",
            evidence=[{"scene_rate": round(scene_rate, 3)}],
        )


class RetentionPredictionFactorProvider:
    """Estimates predicted retention based on content patterns."""

    async def calculate(self, features: dict[str, Any]) -> ViralFactor:
        attention = features.get("attention_features", {})
        timeline = attention.get("timeline", [])

        if not timeline:
            return ViralFactor(
                factor_name="retention_prediction",
                score=0.5,
                weight=0.10,
                description="No attention data",
                evidence=[],
            )

        # Predicted retention correlates with attention curve shape
        # Videos that sustain attention longer = higher retention
        scores = [p["attention_score"] for p in timeline]
        early_avg = sum(scores[:len(scores)//3]) / max(len(scores)//3, 1)
        late_avg = sum(scores[-len(scores)//3:]) / max(len(scores)//3, 1)

        # Retention drops less if late attention is close to early attention
        retention_ratio = late_avg / max(early_avg, 0.01)
        score = min(retention_ratio * 0.8 + 0.2, 1.0)

        return ViralFactor(
            factor_name="retention_prediction",
            score=score,
            weight=0.10,
            description="Predicted viewer retention based on attention sustain",
            evidence=[{"early_attention": round(early_avg, 3), "late_attention": round(late_avg, 3)}],
        )


class ViralScoreEngineAgent(IAgent):
    """Agent that computes comprehensive viral scores for video segments."""

    def __init__(self, providers: list | None = None):
        self.providers = providers or [
            HookFactorProvider(),
            EmotionFactorProvider(),
            CuriosityFactorProvider(),
            VisualPacingFactorProvider(),
            SpeechPacingFactorProvider(),
            NoveltyFactorProvider(),
            RetentionPredictionFactorProvider(),
        ]

    @property
    def agent_id(self) -> str:
        return "viral_score_engine"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return ["viral_scoring", "engagement_prediction", "platform_fit"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        duration = context.get("vision_features", {}).get("duration_seconds", 0)

        # Score the entire video as a single segment for Sprint 3
        # In future: score per-second or per-beat
        viral_score = await self.score_segment((0, duration), context)

        def _serialize_vs(vs: "ViralScore") -> dict:
            return {
                "segment_start": vs.segment_start,
                "segment_end": vs.segment_end,
                "composite_score": vs.composite_score,
                "factors": [f.__dict__ for f in vs.factors],
                "confidence": vs.confidence,
                "platform_fit": vs.platform_fit,
            }

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.3.0",
            capability=self.capability,
            temporal_range=(0.0, duration),
            features={
                "viral_score_map": {
                    "scores": [_serialize_vs(viral_score)],
                    "peak_segments": [_serialize_vs(viral_score)] if viral_score.composite_score > 0.7 else [],
                    "average_score": viral_score.composite_score,
                    "best_platform": self._select_best_platform(viral_score),
                }
            },
        )

    async def score_segment(self, segment: tuple[float, float], features: dict[str, Any]) -> ViralScore:
        """Score a single segment."""
        # Run all providers
        factor_tasks = [p.calculate(features) for p in self.providers]
        factors = await asyncio.gather(*factor_tasks)

        # Compute composite
        composite = sum(f.score * f.weight for f in factors)

        # Platform fit estimation
        platform_fit = {
            "tiktok": self._fit_for_platform(factors, "tiktok"),
            "youtube_shorts": self._fit_for_platform(factors, "youtube_shorts"),
            "facebook_reels": self._fit_for_platform(factors, "facebook_reels"),
            "instagram_reels": self._fit_for_platform(factors, "instagram_reels"),
        }
        best = max(platform_fit, key=platform_fit.get)

        return ViralScore(
            segment_start=segment[0],
            segment_end=segment[1],
            composite_score=min(composite, 1.0),
            factors=factors,
            confidence=0.75,  # heuristic confidence for Sprint 3
            platform_fit=platform_fit,
        )

    def _fit_for_platform(self, factors: list[ViralFactor], platform: str) -> float:
        """Estimate platform fit based on factor composition."""
        factor_map = {f.factor_name: f for f in factors}
        base = 0.5

        if platform == "tiktok":
            # Rewards hook, visual pacing, novelty
            base += factor_map.get("hook", ViralFactor("hook", 0, 0.2, "", [])).score * 0.25
            base += factor_map.get("visual_pacing", ViralFactor("vp", 0, 0.1, "", [])).score * 0.15
            base += factor_map.get("novelty", ViralFactor("nov", 0, 0.1, "", [])).score * 0.10
        elif platform == "youtube_shorts":
            # Rewards emotion, speech pacing, retention
            base += factor_map.get("emotion", ViralFactor("em", 0, 0.15, "", [])).score * 0.20
            base += factor_map.get("speech_pacing", ViralFactor("sp", 0, 0.1, "", [])).score * 0.15
            base += factor_map.get("retention_prediction", ViralFactor("ret", 0, 0.1, "", [])).score * 0.15
        elif platform == "facebook_reels":
            # Rewards emotion, speech, sound-on
            base += factor_map.get("emotion", ViralFactor("em", 0, 0.15, "", [])).score * 0.20
            base += factor_map.get("speech_pacing", ViralFactor("sp", 0, 0.1, "", [])).score * 0.20
        else:
            # instagram_reels — balanced
            base += sum(f.score * f.weight for f in factors) * 0.5

        return min(base, 1.0)

    def _select_best_platform(self, score: ViralScore) -> str:
        return max(score.platform_fit, key=score.platform_fit.get)
