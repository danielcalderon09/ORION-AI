"""Reflection Engine implementation."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.sprint4.reflection_engine.domain.reflection_report import (
    ImprovementSuggestion, ReflectionReport,
)


class ReflectionEngineAgent(IAgent):
    """Analyzes clips against their brief and proposes concrete improvements."""

    @property
    def agent_id(self) -> str:
        return "reflection_engine"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.LEARNING

    def get_capabilities(self) -> list[str]:
        return ["clip_reflection", "improvement_suggestions", "brief_alignment"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        clip = context.get("clip", {})
        brief = context.get("creative_brief", {})
        metrics = context.get("metrics", {})
        retention = context.get("retention_curve", {})

        suggestions = []
        missed = []

        # Alignment: hook duration vs brief
        brief_hook = brief.get("hook_duration", 1.5)
        actual_hook = clip.get("hook_duration", 0)
        if actual_hook > brief_hook + 1.0:
            suggestions.append(ImprovementSuggestion(
                suggestion_id="hook_too_long",
                category="hook",
                severity="moderate",
                description=f"Hook is {actual_hook:.1f}s but brief recommends {brief_hook:.1f}s",
                current_value=actual_hook,
                proposed_value=brief_hook,
                expected_impact=f"+8% attention in first {brief_hook:.1f}s",
                confidence=0.75,
            ))

        # Alignment: pacing vs brief
        brief_pacing = brief.get("pacing", "moderate")
        actual_pacing = clip.get("pacing", "moderate")
        if actual_pacing != brief_pacing:
            suggestions.append(ImprovementSuggestion(
                suggestion_id="pacing_mismatch",
                category="pacing",
                severity="minor",
                description=f"Pacing is '{actual_pacing}' but brief wants '{brief_pacing}'",
                current_value=actual_pacing,
                proposed_value=brief_pacing,
                expected_impact="Better platform alignment",
                confidence=0.60,
            ))

        # Retention: detect predicted drops and suggest trims
        drops = retention.get("critical_drop_points", [])
        for drop_time in drops[:2]:  # top 2 drops
            suggestions.append(ImprovementSuggestion(
                suggestion_id=f"trim_before_drop_{drop_time:.1f}",
                category="trim",
                severity="critical",
                description=f"Predicted retention drop at {drop_time:.1f}s — trim 0.5s before",
                current_value=clip.get("end", 0),
                proposed_value=drop_time - 0.5,
                expected_impact="+12% estimated retention",
                confidence=0.80,
            ))

        # Missed opportunities
        viral_score = metrics.get("viral_score", 0)
        if viral_score < 0.5:
            missed.append("Could have included a stronger audio peak for hook")
        if not clip.get("subtitle_segments"):
            missed.append("No subtitles — platform likely requires them")

        # Calculate alignment score
        alignment = 1.0
        if suggestions:
            alignment -= len([s for s in suggestions if s.severity == "critical"]) * 0.15
            alignment -= len([s for s in suggestions if s.severity == "moderate"]) * 0.08
            alignment -= len([s for s in suggestions if s.severity == "minor"]) * 0.03
        alignment = max(0.1, alignment)

        report = ReflectionReport(
            clip_id=clip.get("clip_id", "unknown"),
            overall_quality=metrics.get("quality_score", 0.5),
            suggestions=suggestions,
            missed_opportunities=missed,
            alignment_score=alignment,
        )

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.4.0",
            capability=self.capability,
            temporal_range=(clip.get("start", 0), clip.get("end", 0)),
            features={
                "reflection_report": {
                    **report.__dict__,
                    "suggestions": [s.__dict__ for s in report.suggestions],
                },
                "suggestion_count": len(suggestions),
                "critical_count": len([s for s in suggestions if s.severity == "critical"]),
            },
        )
