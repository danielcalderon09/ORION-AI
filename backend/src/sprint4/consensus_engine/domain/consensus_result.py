"""Consensus Engine domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AgentVote:
    """Vote from a single agent for a candidate."""
    agent_id: str
    candidate_id: str
    score: float  # 0-1
    reasoning: str
    weight: float  # voting weight in consensus


@dataclass
class ConsensusResult:
    """Result of consensus deliberation."""
    winning_candidate_id: str
    consensus_confidence: float  # 0-1, how much agreement there was
    votes: list[AgentVote]
    runner_up_id: str | None
    runner_up_score: float
    disagreement_flags: list[str]  # reasons if consensus was low
    recommended_action: str  # "accept", "re_generate", "human_review"


class IConsensusEngine(Protocol):
    """Weighted consensus among multiple expert agents."""
    async def deliberate(self, candidates: list[dict], agent_votes: list[list[AgentVote]]) -> ConsensusResult: ...
    async def get_agent_vote(self, agent_id: str, candidate: dict, context: dict[str, Any]) -> AgentVote: ...
