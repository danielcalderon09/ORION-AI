# ADR 015: Sprint 2 — Semantic Intelligence Architecture

## Status
Accepted

## Context
Sprint 1.5 validated that the heuristic-based pipeline produces functional clips. Sprint 2 must replace heuristics with genuine semantic understanding while maintaining full backward compatibility and testability.

## Decision
Implement Sprint 2 in four incremental phases. Each phase must leave the system fully functional with automated tests.

### New Architectural Components

1. **Semantic Memory** (`learning/semantic_memory/`): Independent of Project Brain. Stores dense embeddings, extracted concepts, character profiles, action vocabularies, and scene fingerprints. Reusable across projects via vector similarity (FAISS).

2. **Temporal Identity Tracking** (`cognition/temporal_tracking/`): Assigns persistent identities to detected entities across frames. Tracks character/object trajectories, re-appearances, and state changes over time.

3. **Event Graph** (`cognition/event_graph/`): Evolution of Knowledge Graph. Represents events as first-class nodes with causal edges (`causes`, `enables`, `follows`, `reacts_to`). Supports counterfactual queries.

4. **Explainability Engine** (`production/explainability/`): Captures the full decision provenance of Director AI. Every clip recommendation includes a human-readable justification chain linking attention → narrative → confidence → selection.

### Provider Abstraction
- `IVideoUnderstandingProvider` remains the sole dependency point for multimodal models.
- Adapters: `CLIPProvider` (lightweight, local, embedding-based), `BLIP2Provider` (captioning), `Qwen2VLProvider` (future, heavier).
- Sprint 2 Phase 1 uses CLIP + BLIP-2 as default (downloadable, ONNX-friendly). No hard dependency on Qwen2-VL.

### Phase Plan
| Phase | Focus | Exit Criteria |
|-------|-------|---------------|
| Phase 1 | Multimodal Integration | `VideoUnderstandingAgent` consumes `IVideoUnderstandingProvider`. Semantic Memory populated with real embeddings. All tests pass. |
| Phase 2 | Temporal Tracking | Characters/objects tracked across frames. Identity persistence > 80% on test sequences. |
| Phase 3 | Event Comprehension | Event Graph replaces flat Knowledge Graph. Causal queries functional. |
| Phase 4 | Explainability | Every clip exports `Explanation` object. HTML explainability viewer. |

## Consequences
- **Positivas:** Genuine semantic understanding. Reusable memory across projects. Decisions become auditable and trustable.
- **Negativas:** Increased compute requirements for embedding extraction. FAISS index management adds complexity.

## Notes
- No agent may import `transformers`, `torchvision`, or model libraries directly. Always through adapters.
- Each phase is a vertical slice: domain → application → infrastructure → tests → API update → frontend update.
- The system must remain usable after any single phase; no "big bang" integration at the end.
