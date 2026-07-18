"""Multi Candidate Generator — produces alternative clip versions."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.sprint4.multi_candidate_generator.domain.candidate_variant import (
    CandidateSet, CandidateVariant,
)


class MultiCandidateGeneratorAgent(IAgent):
    """Generates 2-5 variants per selected clip moment."""

    def __init__(self, num_variants: int = 3):
        self.num_variants = num_variants

    @property
    def agent_id(self) -> str:
        return "multi_candidate_generator"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return ["variant_generation", "candidate_diversification", "multi_version"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        selected_clips = context.get("selected_clips", [])
        hooks = context.get("optimized_hooks", [])
        constraints = context.get("creative_constraints", {})
        viral = context.get("viral_score_map", {})

        candidate_sets = []
        for clip in selected_clips:
            clip_id = clip.get("clip_id", "unknown")
            base_start = clip.get("start", 0)
            base_end = clip.get("end", 10)

            # Find hook info for this clip
            hook_info = next(
                (h for h in hooks if h.get("clip_id") == clip_id), None
            )

            candidates = []
            # Variant 1: Original
            candidates.append(CandidateVariant(
                variant_id=f"{clip_id}_v1_original",
                parent_clip_id=clip_id,
                start=base_start,
                end=base_end,
                hook_strategy="original",
                pacing_profile=constraints.get("pacing", "moderate"),
                subtitle_style=constraints.get("caption_style", "animated"),
                has_zoom=False,
                has_jump_cut=False,
                estimated_viral_score=clip.get("viral_score", 0.5),
                estimated_retention=0.5,
            ))

            # Variant 2: Hook-optimized start
            if hook_info:
                opt_start = hook_info.get("optimized_start", base_start)
                candidates.append(CandidateVariant(
                    variant_id=f"{clip_id}_v2_hookopt",
                    parent_clip_id=clip_id,
                    start=opt_start,
                    end=base_end,
                    hook_strategy=hook_info.get("strategy", "jump_to_peak"),
                    pacing_profile=constraints.get("pacing", "fast"),
                    subtitle_style=constraints.get("caption_style", "animated"),
                    has_zoom=True,
                    has_jump_cut=False,
                    estimated_viral_score=clip.get("viral_score", 0.5) * 1.1,
                    estimated_retention=0.55,
                ))

            # Variant 3: Tight cut (shorter, punchier)
            tight_end = base_start + max(5.0, (base_end - base_start) * 0.7)
            candidates.append(CandidateVariant(
                variant_id=f"{clip_id}_v3_tight",
                parent_clip_id=clip_id,
                start=base_start,
                end=tight_end,
                hook_strategy="trim_silence",
                pacing_profile="very_fast",
                subtitle_style="minimal",
                has_zoom=False,
                has_jump_cut=True,
                estimated_viral_score=clip.get("viral_score", 0.5) * 0.95,
                estimated_retention=0.60,
            ))

            # Variant 4: Extended (if narrative allows)
            ext_end = min(base_end + 3.0, base_start + 30.0)
            if ext_end > base_end + 1.0:
                candidates.append(CandidateVariant(
                    variant_id=f"{clip_id}_v4_extended",
                    parent_clip_id=clip_id,
                    start=base_start,
                    end=ext_end,
                    hook_strategy="start_with_reaction",
                    pacing_profile="moderate",
                    subtitle_style="static",
                    has_zoom=False,
                    has_jump_cut=False,
                    estimated_viral_score=clip.get("viral_score", 0.5) * 0.85,
                    estimated_retention=0.45,
                ))

            candidate_sets.append({
                "source_moment_id": clip_id,
                "candidates": [c.__dict__ for c in candidates],
                "generation_strategy": "diversified",
            })

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.4.0",
            capability=self.capability,
            temporal_range=(0.0, 0.0),
            features={
                "candidate_sets": candidate_sets,
                "total_variants": sum(len(s["candidates"]) for s in candidate_sets),
            },
        )
