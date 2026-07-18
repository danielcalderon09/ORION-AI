"""Audience Director — translates audience insights into concrete creative constraints."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.viral_intelligence.audience_model.infrastructure.default_audience_model import DefaultAudienceModel


class AudienceDirectorAgent(IAgent):
    """Agent that translates audience model into actionable creative briefs."""

    def __init__(self, audience_model: DefaultAudienceModel | None = None):
        self.audience_model = audience_model or DefaultAudienceModel()

    @property
    def agent_id(self) -> str:
        return "audience_director"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return ["audience_modeling", "platform_optimization", "creative_briefing"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        platform = context.get("target_platform", "tiktok")
        content_features = context.get("content_features", {})
        video_duration = content_features.get("vision_features", {}).get("duration_seconds", 60)

        brief = self.audience_model.generate_brief(content_features, platform)

        # Generate explicit creative constraints for other agents
        creative_constraints = {
            "platform": platform,
            "max_clips": brief.optimal_clip_count,
            "clip_duration_range": brief.platform_profile.optimal_duration_range,
            "hook_duration": brief.platform_profile.ideal_hook_duration,
            "pacing": brief.platform_profile.pacing_preference,
            "sound_on": brief.platform_profile.sound_importance > 0.7,
            "caption_style": brief.platform_profile.caption_style,
            "attention_span_target": brief.estimated_attention_span,
            "aspect_ratio": brief.platform_profile.preferred_aspect_ratio,
        }

        duration = content_features.get("vision_features", {}).get("duration_seconds", 0)
        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.3.0",
            capability=self.capability,
            temporal_range=(0.0, duration),
            features={
                "audience_brief": brief.__dict__,
                "creative_constraints": creative_constraints,
                "recommendations": brief.content_recommendations,
            },
        )
