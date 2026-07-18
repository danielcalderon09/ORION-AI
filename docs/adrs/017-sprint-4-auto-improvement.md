# ADR 017: Sprint 4 — Auto-Improvement & Continuous Learning

## Status
Accepted

## Context
Sprints 1-3 built a robust pipeline that understands, optimizes, and produces clips. The next evolutionary step is to make Orion self-evaluating and self-correcting. Rather than producing a single version, Orion should generate alternatives, critique them, reflect on them, reach consensus, and learn from both simulated and human feedback.

## Decision
Implement a sixth architectural layer: **Auto-Improvement Layer** composed of six new modules.

### New Modules

1. **Reflection Engine** (`sprint4/reflection_engine/`)
   - Analyzes each generated clip before it is considered final.
   - Compares the clip against the Creative Brief, the Retention Curve, and the Viral Score.
   - Proposes specific improvements: "Trim 1.2s after second 8 to avoid drop-off", "Replace hook with peak at 4.3s", "Increase subtitle size in first 2s".

2. **Critic AI** (`sprint4/critic_ai/`)
   - Independent from Creative Director. Evaluates quality on three axes:
     - **Narrative Quality:** Does the clip tell a coherent micro-story?
     - **Technical Quality:** Are transitions smooth? Is audio balanced?
     - **Retention Quality:** Will viewers stay? Based on simulated curve.
   - Produces a `CritiqueReport` with scores and actionable issues.

3. **Multi Candidate Generator** (`sprint4/multi_candidate_generator/`)
   - Instead of one EDL per clip, produces 2-5 variants per selected moment.
   - Variations include: different hook starts, different durations, different pacing profiles, with/without subtitles.
   - Each candidate is a complete `CandidateClip` with its own viral score and retention estimate.

4. **Consensus Engine** (`sprint4/consensus_engine/`)
   - Orchestrates a weighted vote among all expert agents:
     - Creative Director (weight: 0.30)
     - Audience Director (weight: 0.20)
     - Viral Intelligence (weight: 0.20)
     - Critic AI (weight: 0.15)
     - Reflection Engine (weight: 0.10)
     - Retention Simulator (weight: 0.05)
   - Agents vote on candidates. If disagreement is high, the engine triggers a re-generation round.
   - Final output: a ranked list of candidates with consensus confidence.

5. **Creative Memory** (`sprint4/creative_memory/`)
   - Stores successful editorial decisions indexed by content category (gaming, podcast, etc.).
   - Reusable patterns: "For gaming clips, starting with an audio peak + zoom in gives 15% higher retention".
   - Feeds into Creative Director and Critic AI as prior knowledge.

6. **Human Feedback System** (`sprint4/human_feedback/`)
   - Captures structured feedback from users:
     - Overall rating (1-5)
     - Per-axis ratings: hook, pacing, subtitles, crop
     - Free-text comments (optional)
     - Actions: exported, discarded, re-edited, shared
   - Persists feedback and re-trains (locally) the preference model and Creative Memory weights.

### Architecture Principles
- All new modules implement `IAgent` and depend only on interfaces.
- No module imports another module concretely. Communication via `AgentInput`/`AgentResult`.
- The system remains offline-first. Human feedback is stored locally; cloud sync is a future option.
- The default number of candidates per clip is 3 (configurable). Too many candidates slow down the pipeline.
- Consensus Engine is the bottleneck by design. It serializes the final decision, ensuring traceability.

## Consequences
- **Positivas:** Self-improving system. Alternatives give users choice. Creative Memory accumulates expertise. Feedback loop closes.
- **Negativas:** Pipeline time increases by ~40% due to candidate generation and critique. Compute cost for N candidates.

## Notes
- Phase 1: Reflection + Critic (foundation)
- Phase 2: Multi Candidate Generator + Consensus (decision-making)
- Phase 3: Creative Memory (knowledge accumulation)
- Phase 4: Human Feedback (learning loop)
