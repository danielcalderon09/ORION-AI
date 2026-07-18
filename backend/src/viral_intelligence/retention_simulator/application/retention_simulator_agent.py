"""Retention Simulator implementation."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.viral_intelligence.retention_simulator.domain.retention_curve import (
    RetentionCurve, RetentionPoint,
)


class RetentionSimulatorAgent(IAgent):
    """Agent that simulates viewer retention curves and suggests edits."""

    def __init__(self, platform_profiles: dict | None = None):
        # Default retention curves by platform (empirical approximations)
        self.platform_baselines = platform_profiles or {
            "tiktok": [1.0, 0.85, 0.70, 0.55, 0.45, 0.38, 0.32, 0.28, 0.25, 0.22],
            "youtube_shorts": [1.0, 0.90, 0.78, 0.65, 0.55, 0.48, 0.42, 0.38, 0.35, 0.32],
            "facebook_reels": [1.0, 0.80, 0.60, 0.45, 0.35, 0.28, 0.23, 0.20, 0.18, 0.16],
            "instagram_reels": [1.0, 0.82, 0.65, 0.50, 0.40, 0.33, 0.28, 0.24, 0.21, 0.19],
        }

    @property
    def agent_id(self) -> str:
        return "retention_simulator"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return ["retention_simulation", "drop_off_prediction", "edit_suggestions"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        clips = context.get("selected_clips", [])
        features = context.get("features", {})
        platform = context.get("target_platform", "tiktok")
        attention = features.get("attention_features", {})

        simulated_clips = []
        for clip in clips:
            curve = self._simulate_clip(
                clip_start=clip.get("start", 0),
                clip_end=clip.get("end", 10),
                platform=platform,
                attention=attention,
            )
            simulated_clips.append(curve.__dict__)

        duration = features.get("vision_features", {}).get("duration_seconds", 0)
        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.3.0",
            capability=self.capability,
            temporal_range=(0.0, duration),
            features={
                "retention_curves": simulated_clips,
                "platform": platform,
            },
        )

    def _simulate_clip(self, clip_start: float, clip_end: float, platform: str, attention: dict) -> RetentionCurve:
        """Simulate retention for a single clip."""
        clip_duration = clip_end - clip_start
        baseline = self.platform_baselines.get(platform, self.platform_baselines["tiktok"])

        # Map baseline (10 points) to actual clip duration
        num_points = max(10, int(clip_duration * 2))  # 1 point every 0.5s
        points = []
        critical_drops = []

        # Get attention timeline within clip
        timeline = attention.get("timeline", [])
        clip_attention = [p for p in timeline if clip_start <= p["time"] <= clip_end]

        for i in range(num_points):
            t = clip_start + (i / num_points) * clip_duration
            progress = i / num_points

            # Base retention from platform baseline (interpolated)
            base_idx = int(progress * (len(baseline) - 1))
            base_idx = min(base_idx, len(baseline) - 2)
            frac = progress * (len(baseline) - 1) - base_idx
            base_retention = baseline[base_idx] * (1 - frac) + baseline[base_idx + 1] * frac

            # Adjust based on actual attention at this point
            attention_boost = 0.0
            for att_point in clip_attention:
                if abs(att_point["time"] - t) <= 0.5:
                    attention_boost = (att_point.get("attention_score", 0) - 0.5) * 0.3
                    break

            retention = max(0.05, min(1.0, base_retention + attention_boost))

            # Determine drop risk
            if i > 0:
                prev_retention = points[-1].retained_viewers_pct
                drop = prev_retention - retention
                if drop >= 0.12:
                    drop_risk = "high"
                    suggested = "jump_cut"
                elif drop > 0.06:
                    drop_risk = "medium"
                    suggested = "speed_up"
                else:
                    drop_risk = "low"
                    suggested = None
            else:
                drop_risk = "none"
                suggested = None

            if drop_risk == "high":
                critical_drops.append(t)

            points.append(RetentionPoint(
                time_offset=t - clip_start,
                retained_viewers_pct=retention,
                drop_risk=drop_risk,
                suggested_action=suggested,
            ))

        avg_retention = sum(p.retained_viewers_pct for p in points) / len(points) if points else 0
        # Estimated average watch percentage (area under curve approximation)
        est_watch = avg_retention * 0.8  # heuristic: people watch ~80% of average retention

        return RetentionCurve(
            clip_id=f"clip_{clip_start:.1f}_{clip_end:.1f}",
            points=points,
            average_retention=avg_retention,
            critical_drop_points=critical_drops,
            estimated_avg_watch_pct=est_watch,
        )
