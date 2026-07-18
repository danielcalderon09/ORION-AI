# ADR 014: Sprint 1.5 Validation Protocol

## Status
Accepted

## Context
Before advancing to Sprint 2 (multimodal models replacing heuristics), we must validate that the current heuristic-based architecture produces clips of acceptable quality across diverse video categories. This validation Sprint ensures the foundation is solid.

## Decision
Create Sprint 1.5 as a validation milestone with the following deliverables:
1. **Benchmark Runner:** Automated pipeline execution over reference videos per category (gaming, podcast, sports, music, tutorial, cinematic).
2. **Confidence Scoring:** Every clip selection must include a `confidence.composite` score with decomposed factors and weights, making decisions auditable.
3. **Debug Mode:** A toggle (`debug_mode=True`) that exports a full timeline visualization (JSON + HTML) showing all curves (Attention, Audio, Scene Change, Speech) and Director AI decisions.
4. **Regression Tests:** Baseline comparison suite that saves expected metrics and fails if future changes regress clip count, export success, or resolution compliance.
5. **IVideoUnderstandingProvider:** Interface prepared for Sprint 2 multimodal model integration, with a `DummyVideoUnderstandingProvider` heuristic fallback that can classify genre.

## Consequences
- **Positivas:** Evidence-based approval to move to Sprint 2. Debug timeline enables rapid iteration and troubleshooting. Confidence scores provide transparency.
- **Negativas:** Additional Sprint extends timeline. Requires collecting real test videos.

## Notes
Sprint 1.5 does NOT add new creative features. It adds instrumentation, validation, and interfaces for the next phase. No LLM or multimodal model is used yet.
