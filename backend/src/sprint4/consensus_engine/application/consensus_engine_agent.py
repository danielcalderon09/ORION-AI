"""Consensus Engine — weighted deliberation among expert agents."""

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.sprint4.consensus_engine.domain.consensus_result import (
    AgentVote, ConsensusResult,
)


class ConsensusEngineAgent(IAgent):
    """Aggregates votes from multiple expert agents to select the best candidate."""

    # Voting weights per agent
    WEIGHTS = {
        "creative_director": 0.30,
        "audience_director": 0.20,
        "viral_score_engine": 0.20,
        "critic_ai": 0.15,
        "reflection_engine": 0.10,
        "retention_simulator": 0.05,
    }

    @property
    def agent_id(self) -> str:
        return "consensus_engine"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.COGNITION

    def get_capabilities(self) -> list[str]:
        return ["consensus_deliberation", "weighted_voting", "candidate_ranking"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        candidate_sets = context.get("candidate_sets", [])
        critiques = context.get("critiques", [])  # list of critique reports per candidate
        reflections = context.get("reflections", [])
        viral_scores = context.get("viral_scores", [])
        retention_scores = context.get("retention_scores", [])

        winning_per_moment = []
        for candidate_set in candidate_sets:
            moment_id = candidate_set.get("source_moment_id", "")
            candidates = candidate_set.get("candidates", [])

            if not candidates:
                continue

            # Collect votes for each candidate
            votes_by_candidate: dict[str, list[AgentVote]] = {c["variant_id"]: [] for c in candidates}

            for candidate in candidates:
                cid = candidate["variant_id"]
                # Creative Director votes based on viral score
                viral = candidate.get("estimated_viral_score", 0)
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="creative_director",
                    candidate_id=cid,
                    score=viral,
                    reasoning=f"Viral score: {viral:.2f}",
                    weight=self.WEIGHTS["creative_director"],
                ))

                # Audience Director votes based on platform fit (simplified)
                fit = 0.7  # placeholder; would come from audience model
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="audience_director",
                    candidate_id=cid,
                    score=fit,
                    reasoning=f"Platform fit estimate: {fit:.2f}",
                    weight=self.WEIGHTS["audience_director"],
                ))

                # Viral Score Engine
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="viral_score_engine",
                    candidate_id=cid,
                    score=viral,
                    reasoning=f"Estimated viral: {viral:.2f}",
                    weight=self.WEIGHTS["viral_score_engine"],
                ))

                # Critic AI
                critique = next((c for c in critiques if c.get("candidate_id") == cid), None)
                if critique:
                    critic_score = critique.get("overall_score", 0.5)
                else:
                    critic_score = 0.5
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="critic_ai",
                    candidate_id=cid,
                    score=critic_score,
                    reasoning=f"Critique score: {critic_score:.2f}",
                    weight=self.WEIGHTS["critic_ai"],
                ))

                # Reflection Engine
                reflection = next((r for r in reflections if r.get("clip_id") == candidate.get("parent_clip_id")), None)
                if reflection:
                    align = reflection.get("alignment_score", 0.5)
                    ref_score = align
                else:
                    ref_score = 0.5
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="reflection_engine",
                    candidate_id=cid,
                    score=ref_score,
                    reasoning=f"Brief alignment: {ref_score:.2f}",
                    weight=self.WEIGHTS["reflection_engine"],
                ))

                # Retention Simulator
                ret = candidate.get("estimated_retention", 0.5)
                votes_by_candidate[cid].append(AgentVote(
                    agent_id="retention_simulator",
                    candidate_id=cid,
                    score=ret,
                    reasoning=f"Estimated retention: {ret:.2f}",
                    weight=self.WEIGHTS["retention_simulator"],
                ))

            # Compute weighted scores
            weighted_scores = {}
            for cid, vote_list in votes_by_candidate.items():
                total = sum(v.score * v.weight for v in vote_list)
                weighted_scores[cid] = total

            # Rank
            ranked = sorted(weighted_scores.items(), key=lambda x: x[1], reverse=True)
            winner_id = ranked[0][0]
            winner_score = ranked[0][1]
            runner_up_id = ranked[1][0] if len(ranked) > 1 else None
            runner_up_score = ranked[1][1] if len(ranked) > 1 else 0

            # Consensus confidence: how much winner beats runner-up
            if runner_up_score > 0:
                margin = (winner_score - runner_up_score) / runner_up_score
                confidence = min(1.0, 0.5 + margin)
            else:
                confidence = 1.0

            # Disagreement flags
            flags = []
            if confidence < 0.6:
                flags.append("Low consensus margin — candidates are close in quality")
            if any(v.score < 0.3 for v in votes_by_candidate[winner_id]):
                flags.append("Some agents strongly disliked the winning candidate")

            action = "accept"
            if confidence < 0.5:
                action = "re_generate"
            elif flags:
                action = "human_review"

            result = ConsensusResult(
                winning_candidate_id=winner_id,
                consensus_confidence=confidence,
                votes=[v for vl in votes_by_candidate.values() for v in vl],
                runner_up_id=runner_up_id,
                runner_up_score=runner_up_score,
                disagreement_flags=flags,
                recommended_action=action,
            )
            winning_per_moment.append(result.__dict__)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.4.0",
            capability=self.capability,
            temporal_range=(0.0, 0.0),
            features={
                "consensus_results": winning_per_moment,
                "total_moments": len(winning_per_moment),
                "avg_confidence": sum(r["consensus_confidence"] for r in winning_per_moment) / len(winning_per_moment) if winning_per_moment else 0,
            },
        )
