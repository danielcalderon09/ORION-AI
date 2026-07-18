# ADR 016: Sprint 3 — Viral Intelligence Architecture

## Status
Accepted

## Context
Sprints 1–2 achieved video comprehension and semantic understanding. The goal of Sprint 3 shifts from detection to optimization: producing clips that people want to keep watching and sharing. This is a paradigm shift from understanding to performance.

## Decision
Implement a **Viral Intelligence Layer** composed of six new first-class modules. All follow the existing `IAgent` + `IProvider` + `CapabilityRegistry` pattern.

### New Modules

1. **Viral Score Engine** (`viral_intelligence/viral_score_engine/`)
   - Decomposes viral potential into: Hook, Emotion, Curiosity, Visual Pacing, Speech Pacing, Novelty, Retention Prediction, Platform Fit.
   - Each factor is a separate provider that can be replaced independently.
   - Outputs a `ViralScoreMap` per temporal segment.

2. **Hook Optimizer** (`viral_intelligence/hook_optimizer/`)
   - Reorders or trims the first N seconds of a clip to maximize immediate engagement.
   - Uses the Attention curve, audio energy peaks, and visual dynamism to find the best possible opening.
   - Never modifies the source video; produces a `HookDecision` that the Exporter consumes.

3. **Retention Simulator** (`viral_intelligence/retention_simulator/`)
   - Estimates a retention curve per clip using historical patterns + content features.
   - Detects predicted drop-off points.
   - Triggers automatic re-editing suggestions (trimming, pacing increase, jump cuts) when a drop is predicted.

4. **Audience Model** (`viral_intelligence/audience_model/`)
   - Encapsulates platform-specific behavior patterns: TikTok (3s hook, fast cuts), YouTube Shorts (slightly longer storytelling), Facebook Reels (sound-on emphasis).
   - Informs Creative Director AI and Audience Director.
   - Can be extended with demographic profiles in future sprints.

5. **Creative Director AI** (`viral_intelligence/creative_director_ai/`)
   - Evolution of DirectorAgent. Now optimizes for shareability, not just narrative coherence.
   - Consumes Viral Score + Retention Curve + Audience Model.
   - Decides: which clip to produce, what pacing, where to place the hook, when to cut before a predicted drop-off.

6. **Audience Director** (`viral_intelligence/audience_director/`)
   - New role. Translates Audience Model parameters into concrete editing constraints.
   - Provides a `CreativeBrief` tailored to platform + estimated audience attention span.
   - Works alongside Creative Director AI; does not replace it.

### Architecture Rules
- No module may import TikTok/YouTube/Reels SDKs or APIs. All platform logic is modeled internally.
- The Viral Score is an estimation, not a prediction of real-world performance. It feeds the creative process.
- Hook Optimizer never sacrifices narrative coherence for a hook unless explicitly configured.
- Retention Simulator operates on simulated curves; real-world validation will come in Sprint 4 (Feedback Learning).

## Consequences
- **Positivas:** Content is now optimized for engagement, not just comprehension. Platform-aware output. Explainable viral factors.
- **Negativas:** Viral scoring is inherently heuristic; requires real-world calibration (Sprint 4).

## Notes
- Phase 1: Viral Score Engine + Audience Model (foundations)
- Phase 2: Hook Optimizer (re-orders openings)
- Phase 3: Retention Simulator (predicts and fixes drop-offs)
- Phase 4: Creative Director AI + Audience Director (orchestrates all)
