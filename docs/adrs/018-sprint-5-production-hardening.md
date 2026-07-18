# ADR 018: Sprint 5 — Production Hardening

## Status
Accepted

## Context
Sprints 1-4 established the functional and auto-improving architecture of Orion AI. The system is architecturally mature but not yet production-grade. Before adding more AI capabilities, we must harden the system for reliability, performance, observability, and reproducibility.

## Decision
Implement Sprint 5 as a pure hardening sprint. No new AI models. No new creative features. The goal is to make Orion stable, fast, reproducible, and ready for real-world workloads.

### 11 Hardening Components

1. **Performance Profiler** (`infrastructure/profiler/`)
   - Per-stage wall-clock timing, CPU%, memory delta, I/O bytes.
   - Flame-graph-friendly hierarchical profiling.
   - Exposes `/metrics` endpoint for Prometheus scraping (future).

2. **Memory Manager** (`infrastructure/memory_manager/`)
   - Streaming frame processing: never load entire video into RAM.
   - Automatic eviction of temporal buffers after stage completion.
   - GPU VRAM guard: monitor allocation, spill to CPU when threshold exceeded.
   - Watermark-based triggering of garbage collection.

3. **Checkpoint & Recovery** (`infrastructure/checkpoint/`)
   - After every stage, serialize `ProjectBrain` + intermediate artifacts.
   - On crash, resume from last successful stage instead of restarting.
   - Idempotent stages: re-running a completed stage is a no-op if cache hit.

4. **Pipeline Cache** (`infrastructure/pipeline_cache/`)
   - Content-addressable cache keyed by file hash + stage name + config version.
   - If input video and config haven't changed, skip the stage entirely.
   - Cache entries have TTL and size limits with LRU eviction.

5. **Plugin System** (`infrastructure/plugin_system/`)
   - Plugin interface: `IOrionPlugin` with discovery via filesystem scanning.
   - Plugin types: ModelProvider, Exporter, Analyzer, HookStrategy.
   - Plugins declare their own dependencies and are loaded in isolated sandboxes.
   - Builtin plugins ship with the application; user plugins live in `~/.orion/plugins/`.

6. **Configuration Profiles** (`infrastructure/config_profiles/`)
   - Predefined profiles: `fast` (heuristics only, no GPU), `balanced` (default), `quality` (all models, deep analysis), `gaming`, `podcast`, `sports`, `anime`.
   - Each profile is a complete configuration snapshot: which agents run, which providers they use, timeout settings, quality thresholds.
   - User can create custom profiles that inherit from a base and override specific keys.

7. **Observability Stack** (`infrastructure/observability/`)
   - Metrics: per-agent counters, histograms, gauges exposed as OpenTelemetry.
   - Structured logging with correlation IDs per project.
   - Health check endpoint: `GET /health` reports status of every subsystem.
   - Resource telemetry: CPU, GPU, RAM sampled every 5 seconds during processing.

8. **Stress Test Suite** (`tests/stress/`)
   - Batch processing of 50+ real-world videos of varying formats, resolutions, and durations.
   - Measures: throughput (videos/hour), failure rate, p99 latency, peak memory.
   - Automatic regression detection against previous stress run baselines.

9. **Quality Dashboard** (`api/dashboard/`)
   - REST endpoint `GET /dashboard` returns aggregated quality metrics across all projects.
   - Comparable KPIs: average clip count, average viral score, QA pass rate, export success rate.
   - Trending: week-over-week improvement/decline indicators.

10. **Golden Dataset** (`tests/golden/`)
    - Curated set of 10 representative videos with expected outputs.
    - Used for CI/CD: every commit must pass golden validation.
    - Expected outputs include: number of clips, approximate durations, resolution checks.
    - Versioned alongside the codebase.

11. **Model & Config Versioning** (`infrastructure/versioning/`)
    - Every model provider declares its model version (e.g., `whisper-base-v3`).
    - Config profiles declare a schema version.
    - `reproducibility_manifest.json` is emitted with every project, locking exact versions of all components used.
    - Enables "re-run this project with exactly the same versions" guarantee.

## Consequences
- **Positivas:** Orion becomes a reliable, observable, reproducible product. Users can trust it with long-running jobs. Developers can debug with precision.
- **Negativas:** Significant engineering effort without visible new features. Requires disciplined testing and benchmarking infrastructure.

## Notes
- All hardening components live in `infrastructure/` or `tests/` — no changes to domain or core application logic unless required for instrumentation.
- The default profile for new users is `balanced`. Power users can switch to `quality` or custom profiles.
- Stress tests must run on CI with a 2-hour timeout. If they fail, the commit is blocked.
