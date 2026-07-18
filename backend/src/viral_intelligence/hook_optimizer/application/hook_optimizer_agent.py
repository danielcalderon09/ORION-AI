"""Hook Optimizer implementation."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.viral_intelligence.hook_optimizer.domain.hook_decision import HookDecision


class PeakHookStrategy:
    """Strategy: start at the strongest attention peak within first 5 seconds."""

    async def evaluate(self, candidate_start: float, features: dict) -> float:
        attention = features.get("attention_features", {})
        peaks = attention.get("peaks", [])

        # Find peak closest to candidate_start
        closest_peak = None
        min_dist = float("inf")
        for p in peaks:
            dist = abs(p["time"] - candidate_start)
            if dist < min_dist and p["time"] <= 5.0:  # only first 5 seconds
                min_dist = dist
                closest_peak = p

        if closest_peak is None:
            return 0.1

        # Score: higher attention + closer to start = better
        attention_score = closest_peak.get("attention_score", 0)
        proximity_bonus = max(0, 1.0 - min_dist / 3.0)
        return attention_score * 0.7 + proximity_bonus * 0.3


class SilenceTrimStrategy:
    """Strategy: skip initial silence/low energy."""

    async def evaluate(self, candidate_start: float, features: dict) -> float:
        audio = features.get("audio_features", {})
        rms = audio.get("rms_energy", {}).get("values", [])
        times = audio.get("rms_energy", {}).get("times", [])

        if not rms or not times:
            return 0.3

        # Find first moment with significant energy
        for i, (t, energy) in enumerate(zip(times, rms)):
            if t >= candidate_start and energy > 0.1:
                # Score based on how quickly we hit energy
                time_to_energy = t - candidate_start
                return max(0.1, 1.0 - time_to_energy / 2.0)

        return 0.1


class ReactionHookStrategy:
    """Strategy: start at a reaction moment (high emotion + audio peak)."""

    async def evaluate(self, candidate_start: float, features: dict) -> float:
        attention = features.get("attention_features", {})
        audio = features.get("audio_features", {})

        # Find moments where attention and audio peak coincide
        peaks = attention.get("peaks", [])
        audio_peaks = audio.get("peaks", [])

        best_score = 0.0
        for att_peak in peaks:
            for aud_peak in audio_peaks:
                if abs(att_peak["time"] - aud_peak["time"]) < 0.5:
                    if att_peak["time"] >= candidate_start and att_peak["time"] <= candidate_start + 3.0:
                        score = (att_peak.get("attention_score", 0) + min(aud_peak.get("energy", 0) * 2, 1.0)) / 2
                        best_score = max(best_score, score)

        return best_score


class HookOptimizerAgent(IAgent):
    """Agent that optimizes the opening hook of each clip."""

    def __init__(self, strategies: list | None = None):
        self.strategies = strategies or [
            ("jump_to_peak", PeakHookStrategy()),
            ("trim_silence", SilenceTrimStrategy()),
            ("start_with_reaction", ReactionHookStrategy()),
        ]

    @property
    def agent_id(self) -> str:
        return "hook_optimizer"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return ["hook_optimization", "opening_reordering", "attention_grabbing"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        clips = context.get("selected_clips", [])
        features = context.get("features", {})

        optimized_hooks = []
        for clip in clips:
            start = clip.get("start", 0)
            end = clip.get("end", 10)
            clip_id = clip.get("clip_id", "unknown")

            # Evaluate all strategies
            best_strategy = None
            best_score = 0.0
            best_start = start

            for strategy_name, strategy in self.strategies:
                score = await strategy.evaluate(start, features)
                if score > best_score:
                    best_score = score
                    best_strategy = strategy_name
                    # Compute new start based on strategy
                    if strategy_name == "jump_to_peak":
                        best_start = self._find_peak_start(start, features)
                    elif strategy_name == "trim_silence":
                        best_start = self._find_first_energy(start, features)
                    elif strategy_name == "start_with_reaction":
                        best_start = self._find_reaction_start(start, features)

            # Risk assessment: moving start too far risks losing context
            shift = abs(best_start - start)
            if shift < 1.0:
                risk = "low"
            elif shift < 3.0:
                risk = "medium"
            else:
                risk = "high"
                # Don't shift more than 3 seconds unless explicitly configured
                if shift > 3.0:
                    best_start = start + 3.0
                    risk = "medium"

            decision = HookDecision(
                clip_id=clip_id,
                original_start=start,
                optimized_start=best_start,
                hook_duration=min(3.0, end - best_start),
                hook_score=best_score,
                strategy=best_strategy or "none",
                justification=f"Selected '{best_strategy}' with score {best_score:.2f} to maximize opening engagement",
                risk_level=risk,
            )
            optimized_hooks.append(decision.__dict__)

        duration = features.get("vision_features", {}).get("duration_seconds", 0)
        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.3.0",
            capability=self.capability,
            temporal_range=(0.0, duration),
            features={"optimized_hooks": optimized_hooks},
        )

    def _find_peak_start(self, clip_start: float, features: dict) -> float:
        attention = features.get("attention_features", {})
        peaks = attention.get("peaks", [])
        for p in peaks:
            if p["time"] >= clip_start and p["time"] <= clip_start + 5.0:
                return max(clip_start, p["time"] - 0.5)  # slight lead-in
        return clip_start

    def _find_first_energy(self, clip_start: float, features: dict) -> float:
        audio = features.get("audio_features", {})
        times = audio.get("rms_energy", {}).get("times", [])
        values = audio.get("rms_energy", {}).get("values", [])
        for t, v in zip(times, values):
            if t >= clip_start and v > 0.1:
                return t
        return clip_start

    def _find_reaction_start(self, clip_start: float, features: dict) -> float:
        attention = features.get("attention_features", {})
        audio = features.get("audio_features", {})
        peaks = attention.get("peaks", [])
        audio_peaks = audio.get("peaks", [])

        for att_p in peaks:
            for aud_p in audio_peaks:
                if abs(att_p["time"] - aud_p["time"]) < 0.5:
                    if clip_start <= att_p["time"] <= clip_start + 5.0:
                        return max(clip_start, att_p["time"] - 0.3)
        return clip_start
