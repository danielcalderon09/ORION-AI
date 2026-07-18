# Orion AI Validation Roadmap

> **Status:** Architecture frozen after Sprint 5. Focus shifted from feature development to real-world validation, optimization, and operational excellence.

---

## Architecture Freeze Policy

Starting from the approval of Sprint 5, the core architecture of Orion AI is **frozen**. This means:

- **No new architectural layers** will be added without an ADR and explicit approval.
- **Interfaces (Ports) are stable**: `IAgent`, `IProvider`, `IExporter`, `ICreativeMemory`, `IEventGraph`, `IExplainabilityProvider`, `IViralScorer`, etc.
- **Dependency injection graph is fixed**: New implementations must conform to existing interfaces.
- **Orchestrator contract is stable**: `Sprint5Orchestrator.process_video()` signature is the production API.

### What CAN Evolve

| Area | Evolution Path |
|------|---------------|
| **Models** | Swap Faster Whisper Base for Large-V3. Replace CLIP with SigLIP. Upgrade retention predictors. |
| **Datasets** | Expand Golden Dataset from 10 to 100+ curated videos. Add domain-specific datasets (gaming, podcast). |
| **Parameters** | Tune confidence thresholds, pacing profiles, viral score weights via Creative Memory + Bayesian updates. |
| **UX** | Frontend refinements, export formats, preview quality, dashboard widgets. |
| **Providers** | New `IProvider` implementations (e.g., ONNX Runtime, TensorRT, Core ML) without changing interfaces. |
| **Profiles** | Add new config profiles (e.g., `documentary`, `music_video`) to `ConfigProfileManager`. |

### What CANNOT Change Without ADR

- Adding a new agent category (e.g., "Distribution Agent") = New architectural layer = ADR required.
- Changing the `VideoProject` entity schema = Database migration = ADR required.
- Modifying the `EventBus` contract = Breaking change = ADR required.

---

## Validation Pillars

### Pillar 1: Automated Quality Gates (CI/CD)

Every commit to `main` must pass:

1. **Lint & Format**: `ruff` + `black` + `mypy`
2. **Unit Tests**: < 2s per test, > 80% coverage target
3. **Integration Tests**: Sprint 2-5 agent contracts validated
4. **Golden Dataset**: 10 curated videos with expected outputs (regression)
5. **Stress Tests**: Batch of 5+ videos, measure throughput and failure rate

**Artifacts published per run**:
- Coverage report (HTML + JSON)
- Test result XML (JUnit format)
- Profiling report (HTML with bottleneck visualization)
- Performance metrics JSON (duration, memory, CPU per stage)

### Pillar 2: Profiling & Optimization

**Goal**: Identify and eliminate bottlenecks before they reach users.

| Tool | Purpose |
|------|---------|
| `PerformanceProfiler` | Per-stage wall-clock timing, CPU%, memory delta |
| `memory-profiler` | Line-by-line memory consumption in hot paths |
| `line-profiler` | Line-by-line CPU time for agent `execute()` methods |
| `PipelineCache` | Avoid recomputation via content-addressable cache |
| `CheckpointManager` | Resume failed runs without reprocessing |

**Profiling workflow**:
1. Run `scripts/profile_pipeline.py --simulate` in CI to generate baseline.
2. Compare against historical baselines on every PR.
3. Flag regressions > 10% in any stage duration.

### Pillar 3: Resource Optimization

**Current constraints**:
- CPU: Primary processing medium (MVP is CPU-only).
- RAM: Target < 4GB peak for `balanced` profile.
- Disk: Models auto-download to `~/.orion/models/`. Cache eviction at 80% capacity.

**Optimization targets**:
| Metric | Target | Current (est.) |
|--------|--------|----------------|
| Avg processing time / 1-min video | < 30s | ~60s |
| Peak RAM / video | < 2GB | ~1.5GB |
| Throughput (batch) | > 60 videos/hour | ~30/hour |
| Cache hit rate | > 40% | 0% (fresh) |

**Strategies**:
- **StreamingBuffer**: Process video in chunks instead of loading full frames.
- **ONNX Runtime**: Quantize vision models to INT8 for 2-3x speedup.
- **Batch STT**: Use Faster Whisper's built-in batching for multi-video jobs.
- **VRAM guard**: If GPU available, offload vision encoder; fallback to CPU gracefully.

### Pillar 4: Real-World Validation

**Phase 1: Internal Dataset (Week 1-2)**
- Process 50+ internal videos spanning genres: gaming, podcast, sports, cooking, vlog.
- Collect metrics: processing time, clip count, viral score distribution.
- Tune `ConfigProfileManager` defaults based on results.

**Phase 2: Golden Dataset Expansion (Week 3-4)**
- Expand from 10 to 50 videos with human-verified "best moments".
- Measure: Does Orion select the same moments as human editors?
- Metric: Top-3 overlap score (Orion clips vs human picks).

**Phase 3: Pilot Users (Week 5-8)**
- 10-20 content creators use Orion on their own videos.
- Collect structured feedback via `FileSystemFeedbackCollector`.
- Track: publish rate (how many Orion clips get posted), engagement delta.
- Run `SimpleFeedbackLearner` weekly to update Creative Memory priors.

**Phase 4: Public Beta (Week 9-12)**
- Open access with telemetry (opt-in).
- A/B test: Orion clips vs manual clips for same video.
- Survival metric: > 60% of users process > 3 videos in first week.

---

## CI/CD Pipeline Architecture

```
Push/PR to main/develop
        |
        v
+-------------------+
| Lint + Format     |  ruff, black, mypy
+-------------------+
        |
        v
+-------------------+
| Unit Tests        |  pytest + coverage
+-------------------+
        |
        v
+-------------------+
| Integration Tests |  Sprint 2-5 contracts
+-------------------+
        |
        +----------+--------------------------+
        |          |                          |
        v          v                          v
+----------------+  +----------------+  +----------------+
| Golden Dataset |  | Stress Tests   |  | Profiling      |
| (on [golden])  |  | (nightly)      |  | (on main)      |
+----------------+  +----------------+  +----------------+
```

**Published artifacts**:
- `test-report.json`: Combined pass/fail summary.
- `coverage.xml`: For Codecov upload.
- `profiling-reports/profile.html`: Interactive bottleneck chart.
- `stress-test-metrics/`: Throughput and failure rates.

---

## Performance Benchmarks

### Baseline (Current, CPU-only)

| Stage | Duration (est.) | % of Total |
|-------|-----------------|------------|
| Vision (frame extraction) | 2.5s | 15% |
| Audio (spectrogram, RMS) | 1.2s | 7% |
| Speech (Faster Whisper Base) | 3.8s | 23% |
| Video Understanding (CLIP) | 4.1s | 25% |
| Viral Score | 0.8s | 5% |
| Creative Director | 1.5s | 9% |
| Consensus + Export | 2.3s | 14% |
| **Total** | **~16s** | **100%** |

*For a 60-second video on a modern 8-core CPU.*

### Target (Optimized)

| Optimization | Expected Impact |
|--------------|-----------------|
| ONNX INT8 quantization (Vision) | -40% vision time |
| Batch STT (3 videos) | -30% speech time per video |
| PipelineCache hit (reprocess) | -50% total time |
| StreamingBuffer (no full load) | -20% memory, -10% time |
| GPU offload (if available) | -60% vision + understanding |

---

## Success Criteria for Public Release

| Criterion | Threshold |
|-----------|-----------|
| Test pass rate (CI) | > 95% |
| Code coverage | > 80% |
| Golden Dataset top-3 overlap | > 70% |
| Pilot user publish rate | > 60% |
| Avg processing time (1-min video) | < 30s |
| Crash rate (pilot) | < 2% |
| NPS score (pilot survey) | > 40 |

---

## Roles & Responsibilities

| Role | Focus |
|------|-------|
| **ML Engineer** | Model upgrades (Whisper, CLIP, retention predictors). Golden dataset curation. |
| **Backend Engineer** | CI/CD hardening, profiling, cache optimization, batch processing. |
| **Frontend Engineer** | UX for feedback collection, dashboard for metrics, preview player. |
| **DevOps** | CI/CD maintenance, artifact storage, monitoring alerts. |
| **Product** | Pilot user recruitment, A/B test design, success metrics tracking. |

---

## Checklist: Before Public Beta

- [ ] CI/CD passing on every commit for 2 weeks straight.
- [ ] Golden Dataset expanded to 50 videos with > 70% overlap.
- [ ] Stress tests stable at > 60 videos/hour throughput.
- [ ] Profiling reports reviewed; no stage > 40% of total time.
- [ ] All ADRs 001-018 reviewed and marked `accepted`.
- [ ] Privacy policy and telemetry opt-in implemented.
- [ ] Onboarding flow for first-time users tested with 5 pilots.
- [ ] Crash reporting (Sentry or equivalent) integrated.
- [ ] Model auto-download tested on fresh Windows/macOS/Linux installs.

---

*Document version: 1.0*
*Last updated: 2026-06-29*
*Architecture frozen: Sprint 5 approved*
