"""Critic AI implementation with independent quality axes."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.sprint4.critic_ai.domain.critique_report import (
    CriticScore, CritiqueReport,
)


class NarrativeCritic:
    """Evaluates narrative coherence of a candidate clip."""

    async def critique(self, candidate: dict, context: dict) -> CriticScore:
        narrative = context.get("narrative_features", {})
        structure = narrative.get("narrative_structure", {})
        beats = structure.get("beats", [])

        clip_start = candidate.get("start", 0)
        clip_end = candidate.get("end", 10)

        # Count narrative beats inside clip
        beats_inside = [b for b in beats if clip_start <= b.get("timestamp", 0) <= clip_end]
        beat_density = len(beats_inside) / max(clip_end - clip_start, 1)

        # Ideal: 1-3 beats per 10 seconds
        if 0.1 <= beat_density <= 0.3:
            score = 0.85
            issues = []
            strengths = ["Good narrative rhythm"]
        elif beat_density < 0.1:
            score = 0.55
            issues = ["Low narrative density — may feel static"]
            strengths = []
        else:
            score = 0.70
            issues = ["Too many narrative beats — may feel chaotic"]
            strengths = ["High narrative activity"]

        # Hook start coherence
        hook_start = candidate.get("hook_optimized_start", clip_start)
        if abs(hook_start - clip_start) > 2.0:
            issues.append("Large hook shift may sacrifice setup context")
            score -= 0.1

        return CriticScore(
            axis="narrative",
            score=max(0.1, score),
            issues=issues,
            strengths=strengths,
        )


class TechnicalCritic:
    """Evaluates technical quality: transitions, audio balance, framing."""

    async def critique(self, candidate: dict, context: dict) -> CriticScore:
        qa = candidate.get("qa_result", {})
        issues = []
        strengths = []
        score = 0.9

        if not qa.get("passed", True):
            issues.append("QA validation failed")
            score -= 0.3

        checks = qa.get("checks", [])
        for check in checks:
            if not check.get("passed", True):
                issues.append(f"Failed: {check.get('name', 'unknown')}")
                score -= 0.1

        framing = candidate.get("framing", {})
        if framing:
            strengths.append("Framing computed by DoP AI")
        else:
            issues.append("No framing data — default center crop used")
            score -= 0.05

        # Duration sanity
        duration = candidate.get("end", 0) - candidate.get("start", 0)
        if duration < 3.0:
            issues.append("Clip very short — may feel abrupt")
            score -= 0.1
        elif duration > 90.0:
            issues.append("Clip very long — exceeds typical platform limits")
            score -= 0.05

        return CriticScore(
            axis="technical",
            score=max(0.1, score),
            issues=issues,
            strengths=strengths,
        )


class RetentionCritic:
    """Evaluates predicted retention quality."""

    async def critique(self, candidate: dict, context: dict) -> CriticScore:
        retention = context.get("retention_curve", {})
        points = retention.get("points", [])
        avg_retention = retention.get("average_retention", 0)
        watch_pct = retention.get("estimated_avg_watch_pct", 0)

        issues = []
        strengths = []
        score = avg_retention

        if watch_pct < 0.3:
            issues.append(f"Low estimated watch-through ({watch_pct:.0%}) — consider trimming")
            score -= 0.15
        elif watch_pct > 0.7:
            strengths.append(f"Strong estimated watch-through ({watch_pct:.0%})")
            score += 0.05

        drops = retention.get("critical_drop_points", [])
        if len(drops) > 2:
            issues.append(f"Multiple retention drops detected ({len(drops)})")
            score -= 0.1
        elif len(drops) == 0:
            strengths.append("No critical retention drops predicted")

        return CriticScore(
            axis="retention",
            score=min(1.0, max(0.1, score)),
            issues=issues,
            strengths=strengths,
        )


class EngagementCritic:
    """Evaluates engagement potential based on viral factors."""

    async def critique(self, candidate: dict, context: dict) -> CriticScore:
        viral = candidate.get("viral_score", 0)
        attention = context.get("attention_features", {})
        peaks = attention.get("peaks", [])
        peaks_in_clip = [p for p in peaks if candidate.get("start", 0) <= p["time"] <= candidate.get("end", 0)]

        score = viral
        issues = []
        strengths = []

        if viral < 0.4:
            issues.append(f"Low viral score ({viral:.2f}) — limited shareability")
        elif viral > 0.8:
            strengths.append(f"High viral score ({viral:.2f}) — strong shareability potential")

        if len(peaks_in_clip) == 0:
            issues.append("No attention peaks inside clip — may feel flat")
            score -= 0.1
        else:
            strengths.append(f"{len(peaks_in_clip)} attention peaks inside clip")

        return CriticScore(
            axis="engagement",
            score=min(1.0, max(0.1, score)),
            issues=issues,
            strengths=strengths,
        )


class CriticAIAgent(IAgent):
    """Independent critic that evaluates clip candidates on multiple axes."""

    def __init__(self):
        self.critics = [
            NarrativeCritic(),
            TechnicalCritic(),
            RetentionCritic(),
            EngagementCritic(),
        ]

    @property
    def agent_id(self) -> str:
        return "critic_ai"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.LEARNING

    def get_capabilities(self) -> list[str]:
        return ["narrative_critique", "technical_critique", "retention_critique", "engagement_critique"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        candidate = context.get("candidate", {})
        candidate_context = context.get("context", {})

        # Run all critics
        axis_scores = []
        for critic in self.critics:
            score = await critic.critique(candidate, candidate_context)
            axis_scores.append(score)

        overall = sum(s.score for s in axis_scores) / len(axis_scores) if axis_scores else 0.5
        fatal = []
        recommendations = []
        for s in axis_scores:
            fatal.extend(s.issues)
            if s.score < 0.5:
                recommendations.append(f"Improve {s.axis}: {', '.join(s.issues[:2])}")

        report = CritiqueReport(
            candidate_id=candidate.get("clip_id", "unknown"),
            overall_score=overall,
            axis_scores=axis_scores,
            fatal_flaws=[f for f in fatal if "failed" in f.lower() or "error" in f.lower()],
            recommendations=recommendations,
            confidence=0.75,
        )

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.4.0",
            capability=self.capability,
            temporal_range=(candidate.get("start", 0), candidate.get("end", 0)),
            features={
                "critique_report": {
                    "candidate_id": report.candidate_id,
                    "overall_score": report.overall_score,
                    "axis_scores": [{"axis": s.axis, "score": s.score, "issues": s.issues, "strengths": s.strengths} for s in axis_scores],
                    "fatal_flaws": report.fatal_flaws,
                    "recommendations": report.recommendations,
                },
                "passed": len(report.fatal_flaws) == 0 and report.overall_score >= 0.4,
            },
        )
