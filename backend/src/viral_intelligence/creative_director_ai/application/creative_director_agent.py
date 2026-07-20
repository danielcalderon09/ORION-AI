"""Creative Director AI — optimizes clips for engagement and shareability."""

from dataclasses import dataclass
from uuid import uuid4

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent


@dataclass
class CreativeDirectorConfig:
    target_platform: str = "tiktok"
    min_clip_duration: float = 5.0
    max_clip_duration: float = 60.0
    preferred_clip_count: int = 3
    prioritize_viral_score: bool = True
    prioritize_retention: bool = True
    optimize_hooks: bool = True
    auto_trim_drops: bool = True


class CreativeDirectorAgent(IAgent):
    """Evolution of DirectorAgent optimized for content performance, not just narrative."""

    def __init__(self, config: CreativeDirectorConfig | None = None):
        self.config = config or CreativeDirectorConfig()

    @property
    def agent_id(self) -> str:
        return "creative_director"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return [
            "viral_optimization",
            "engagement_maximization",
            "platform_adaptation",
            "retention_aware_editing",
        ]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        attention = context.get("attention_features", {})
        narrative = context.get("narrative_features", {})
        speech = context.get("speech_features", {})
        viral = context.get("viral_score_map", {})
        retention = context.get("retention_curves", [])
        hooks = context.get("optimized_hooks", [])
        audience_constraints = context.get("creative_constraints", {})

        # Build creative brief incorporating viral intelligence
        brief = self._generate_creative_brief(context)

        # Select clips based on viral score first, then narrative
        clips = self._select_clips_viral_optimized(
            attention, narrative, viral, retention, hooks, audience_constraints
        )

        # Generate edit decisions with retention-aware trimming
        decisions = self._build_edit_decisions(clips, speech, retention, audience_constraints)

        features = {
            "creative_brief": brief,
            "selected_clips": clips,
            "edit_decisions": decisions,
            "optimization_strategy": "viral_maximization",
            "platform": self.config.target_platform,
        }

        duration = context.get("vision_features", {}).get("duration_seconds", 0)
        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.3.0",
            capability=self.capability,
            temporal_range=(0.0, duration),
            features=features,
        )

    def _generate_creative_brief(self, context: dict) -> dict:
        genre = context.get("video_understanding", {}).get("genre", "general")
        audience = context.get("creative_constraints", {})

        return {
            "genre": genre,
            "platform": audience.get("platform", "tiktok"),
            "target_duration_range": audience.get("clip_duration_range", (15, 60)),
            "style_profile": "viral_optimized",
            "pacing": audience.get("pacing", "fast"),
            "hook_duration": audience.get("hook_duration", 1.5),
            "audio_treatment": "enhance_peaks",
            "subtitle_style": audience.get("caption_style", "animated"),
            "optimization_goal": "maximize_retention_and_shares",
        }

    def _select_clips_viral_optimized(
        self, attention, narrative, viral, retention, hooks, constraints
    ) -> list[dict]:
        """Select clips prioritizing viral score, with retention and hook optimization."""
        peaks = attention.get("peaks", [])
        climax = narrative.get("climax_candidates", [])
        viral_scores = viral.get("scores", [])
        max_clips = constraints.get("max_clips", self.config.preferred_clip_count)
        max_duration = constraints.get("clip_duration_range", [5, 60])[1]
        min_duration = constraints.get("clip_duration_range", [5, 60])[0]

        # Build candidate list with viral scoring
        candidates = []
        for p in peaks:
            # Find associated viral score
            vs = next(
                (v for v in viral_scores if v["segment_start"] <= p["time"] <= v["segment_end"]),
                None,
            )
            viral_score = vs["composite_score"] if vs else p["attention_score"]

            candidates.append({
                "timestamp": p["time"],
                "attention_score": p["attention_score"],
                "viral_score": viral_score,
                "source": "attention",
            })

        for c in climax:
            candidates.append({
                "timestamp": c["timestamp"],
                "attention_score": c["score"],
                "viral_score": c["score"],
                "source": "narrative",
            })

        # Sort by viral score (primary) then attention
        candidates.sort(key=lambda x: (x["viral_score"], x["attention_score"]), reverse=True)

        selected = []
        used_times = []
        for c in candidates:
            t = c["timestamp"]
            too_close = any(abs(t - u) < min_duration for u in used_times)
            if too_close:
                continue

            # Apply hook optimization if available
            hook_start = t
            hook_info = next(
                (h for h in hooks if abs(h["original_start"] - t) < 1.0), None
            )
            if hook_info and self.config.optimize_hooks:
                hook_start = hook_info["optimized_start"]

            # Apply retention-based trimming
            end = t + min(max_duration, 15.0)
            if self.config.auto_trim_drops and retention:
                # Find retention curve for this time range and trim at critical drop
                for rc in retention:
                    drops = rc.get("critical_drop_points", [])
                    for drop in drops:
                        if hook_start < drop < end:
                            end = drop - 0.5  # cut just before drop
                            break
                    break

            clip = {
                "clip_id": str(uuid4()),
                "start": max(0, hook_start - 0.5),
                "end": end,
                "viral_score": c["viral_score"],
                "attention_score": c["attention_score"],
                "source": c["source"],
                "hook_optimized": hook_info is not None,
            }
            selected.append(clip)
            used_times.append(t)

            if len(selected) >= max_clips:
                break

        return selected

    def _build_edit_decisions(self, clips, speech, retention, constraints):
        decisions = []
        for clip in clips:
            # Find subtitles
            subtitles = []
            for seg in speech.get("segments", []):
                if clip["start"] <= seg["start"] <= clip["end"]:
                    subtitles.append(seg)

            # Pacing based on viral score and platform
            viral = clip.get("viral_score", 0.5)
            if viral > 0.8:
                pacing = "very_fast"
            elif viral > 0.6:
                pacing = "fast"
            else:
                pacing = constraints.get("pacing", "moderate")

            # Check if retention curve suggests trimming
            trim_suggestion = None
            if retention and self.config.auto_trim_drops:
                for rc in retention:
                    drops = rc.get("critical_drop_points", [])
                    if drops:
                        # Suggest trimming at first drop
                        trim_suggestion = max(clip["start"], min(drops[0] - 0.5, clip["end"]))

            decision = {
                "clip_id": clip["clip_id"],
                "temporal_range": (clip["start"], clip["end"]),
                "pacing": pacing,
                "subtitle_segments": subtitles,
                "audio_enhance": True,
                "hook_optimized": clip.get("hook_optimized", False),
                "retention_trim": trim_suggestion,
            }
            decisions.append(decision)

        return decisions
