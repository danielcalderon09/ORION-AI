# ORION Production Architecture Audit

## 1. Executive summary

Phase 5I audited the production runtime and all durable prompt-to-video bounded
contexts without adding a creative capability or exercising a live integration.
The baseline is suitable for the later audio, editor, and frontend phases after a
small stabilization set. The changes remove one invalid application-layer reverse
dependency, make the corresponding import graph deterministic, prevent
incompatible planning artifacts from being overwritten, tighten remote-job state
invariants, bound durable and HTTP reads, and clarify HTTP-client ownership.

No confirmed CRITICAL issue remains unresolved. No schema version changed, no
database migration was introduced, and existing manifest formats remain
backward-compatible.

## 2. Repository baseline SHA

- Branch: `main`
- Starting `HEAD`: `3cad850f27bc0dab65dec916d099028ba5c5633d`
- Starting `origin/main`: `34fc9e2e8a4f3fb1fec40515c56e6e44116ec570`
- Baseline provenance: the clean worktree was one commit ahead of `origin/main`;
  that commit is the completed Phase 5F.3 secure asset-publishing work.
- Baseline worktree: clean; `git diff --check` passed.

## 3. Bounded-context inventory

The immutable inventory found 254 Python files under `backend/src/production`
after the architecture guard was added, across these 18 top-level packages:

| Package | Primary responsibility |
| --- | --- |
| `api` | Feature-gated HTTP boundary and response mapping |
| `application` | Commands, orchestration, ports, results, and use cases |
| `domain` | Production job, artifact, edit-package, and value contracts |
| `runtime` | Leases, dispatch, recovery, execution, and worker lifecycle |
| `infrastructure` | SQLAlchemy adapters and shared optional HTTP transport |
| `composition` | Concrete adapter selection and lifecycle ownership |
| `planning` | Durable production-plan generation |
| `scripting` | Durable script generation |
| `scene_planning` | Durable scene and shot planning |
| `visual_asset_planning` | Durable visual-asset requirements |
| `binary_assets` | Provider-neutral durable binary storage |
| `image_acquisition` | Verified image acquisition and manifest recovery |
| `video_clip_generation` | Simulated clips and optional remote-job workflow |
| `asset_publishing` | Temporary publication contracts and local publisher |
| `explainability` | Legacy decision-explanation models and service |
| `creative_brief` | Earlier creative-brief contracts |
| `director_ai` | Earlier director-domain contracts |
| `dop_ai` | Earlier cinematography-domain contracts |

The audit also covered `backend/tests`, `docs`, and `pyproject.toml`.

## 4. Dependency map

The intended layered dependency map is:

```text
api ------------------------> application/domain
composition ----------------> application ports + every selected adapter
runtime --------------------> application/domain + injected stage contracts
context handlers -----------> their own ports/models + stable upstream read models
provider adapters ----------> context ports + optional shared HTTP transport
persistence adapters -------> application/domain contracts
binary asset adapters ------> binary/domain/application contracts
```

Important cross-context edges were classified as follows:

| Edge | Classification | Rationale |
| --- | --- | --- |
| `image_acquisition -> binary_assets` | Valid contract dependency | Images are verified and stored through the binary store contracts. |
| `image_acquisition -> visual_asset_planning` | Valid read-model dependency | Acquisition consumes the durable visual-asset plan. |
| `video_clip_generation -> image_acquisition` | Valid read-model dependency | Video consumes verified image manifest entries, not the image provider. |
| `video_clip_generation -> binary_assets` | Valid contract dependency | Clips use provider-neutral binary metadata and integrity contracts. |
| `asset_publishing -> image_acquisition/video_clip_generation` | Valid read-model dependency | The collector reads stable manifest models; it imports no OpenRouter provider. |
| `asset_publishing -> binary_assets` | Valid contract dependency | Publication reads verified local assets through binary contracts. |
| `runtime -> stage contexts` | Acceptable orchestration dependency | Runtime dispatches typed handlers and stage boundaries. |
| `composition -> all adapters` | Acceptable composition dependency | This is the primary concrete-selection location. |
| stage `providers -> production.infrastructure.openai_compatible` | Acceptable adapter dependency | Only optional real-provider modules use the shared transport. |
| `application -> runtime/infrastructure` | Invalid reverse dependency, fixed | Execution and decision-persistence protocols now belong to application ports. |

`explainability` is currently isolated from the durable prompt-to-video manifest
chain; it does not create a reverse dependency on provider infrastructure.

## 5. Valid dependency directions

The AST quality gate enforces the most important rules with the standard library:

- production code cannot import test code;
- domain contracts cannot import runtime, composition, infrastructure, HTTP, API,
  or SQL adapters;
- application core cannot import runtime, composition, infrastructure, HTTP, API,
  or SQL adapters;
- provider modules cannot import pipeline orchestration, composition, or runtime
  handlers;
- simulated providers cannot import HTTP or real-provider transports;
- `binary_assets` and `asset_publishing` cannot import image/video provider
  adapters.

The runtime registry factory now keeps stage-specific handler annotations behind
`TYPE_CHECKING`. This removes a real eager import cycle rather than hiding it in a
function-local import.

## 6. Detected risks

### CRITICAL

- Application services depended on concrete runtime and persistence types. This
  was an invalid reverse dependency and masked an eager runtime import cycle.
- Local planning writes could replace an incompatible durable artifact at the
  same idempotency path.
- General provider composition accepted arbitrary HTTPS hosts and paths, which
  could direct an Authorization header to an unintended endpoint.

All confirmed CRITICAL findings were fixed.

### IMPORTANT

- Shared planning/script/scene/visual HTTP responses were not size-bounded and
  accepted non-standard `NaN`/`Infinity` JSON.
- Shared and image providers closed caller-injected clients.
- Several small durable manifest/sidecar readers buffered an entire file before
  checking its configured limit.
- Planning, scripting, and scene-plan atomic writes had inconsistent directory
  `fsync` behavior.
- Remote video records did not uniformly require timezone-aware timestamps, and
  terminal checkpoints were not fully immutable.
- The image request path did not explicitly override redirect behavior per
  request.
- Legacy integration/stress tests wrote checkpoints, caches, reports, and project
  state to the real user workspace.
- The legacy video controller opened a process-lifetime file handler during module
  import, coupling import order to filesystem permissions.

These were stabilized with focused behavioral tests.

### MINOR

- Fourteen legacy empty `__init__.py` files contain a UTF-8 BOM. Python imports
  them correctly, but naive UTF-8 AST tooling does not; the architecture test uses
  `utf-8-sig`.
- Legacy explainability persistence still uses an ad-hoc JSON writer and global
  settings. It is outside the active durable manifest pipeline.
- Repository-wide Ruff debt predates Phase 5I.

## 7. Stabilization changes made

- Added application-owned execution and decision-persistence ports and
  application-level concurrency/integrity errors; persistence exceptions remain
  compatible adapter specializations.
- Moved type-only stage imports out of runtime execution, eliminating the exposed
  circular import.
- Hardened the shared optional HTTP transport with bounded streaming reads,
  strict finite JSON, explicit redirect denial, environment-proxy isolation for
  owned clients, idempotent close, and caller-owned client preservation.
- Applied the same lifecycle and redirect rules to image acquisition.
- Pinned composed OpenRouter endpoints to the official host and `/api/v1` path
  with no credentials, query, fragment, or alternate port.
- Made local planning writes write-once/idempotent and aligned directory durability
  for planning, scripting, and scene artifacts.
- Replaced unbounded reads for binary sidecars, publication manifests, video
  sidecars, remote jobs, and reconciliation inputs with bounded reads.
- Added timezone and transition invariants to durable remote video records without
  changing schema version or serialized field names.
- Fixed the one production MyPy annotation error found at baseline.
- Added focused architecture, configuration-default, lifecycle, idempotency,
  timestamp, and terminal-state tests.
- Isolated integration and stress suites to pytest temporary roots, made memory
  stage eviction release its registered buffer, and removed import-time log-file
  ownership from the legacy controller.

## 8. Deliberate duplications retained

The following are category A, deliberate bounded-context duplication:

- planning, scripting, scene, visual-asset, image, video, binary-sidecar, remote
  job, and publication serializers;
- context-specific manifest models and typed exceptions;
- context-specific CAS/checkpoint transition checks;
- context-specific workspace path and recovery validation.

Atomic-write and bounded-read helpers remain local where their limits, exception
types, or state semantics differ. The already-owned shared OpenAI-compatible
transport is the only category B primitive extended in this phase. Category C
drift in bounded reads, `fsync`, and planning write-once behavior was corrected.
No universal manifest framework or merged manifest model was created.

## 9. Deferred improvements

- `Settings` still creates configured directories during construction, including
  the module-level default instance. Removing that legacy side effect requires a
  separately planned startup/lifecycle compatibility change.
- A failure late in synchronous container construction can leave adapters created
  earlier in that same construction path awaiting process cleanup. A safe solution
  requires a broader resource-construction transaction, not a Phase 5I patch.
- Legacy explainability JSON persistence should eventually adopt an injected,
  atomic store if that context rejoins the production pipeline.
- The legacy BOM files and repository-wide formatting/lint debt should be handled
  in a dedicated mechanical cleanup to avoid mixing hundreds of unrelated edits
  with this architecture baseline.
- Legacy integration/golden/stress media coverage exercises locally installed
  FFmpeg. Phase 5I did not add that dependency, and no test uses DaVinci, a cloud
  service, or a paid provider.

None of these deferred items permits billing, manifest corruption, or a live
provider request under default configuration.

## 10. Security posture

- Locally reachable Git history and the working tree were scanned
  deterministically for OpenRouter-style keys, generic long `sk-` values, AWS
  access keys, Google API keys, and private-key markers; no real credential was
  found.
- Test credentials are visibly fake and are used only with `httpx.MockTransport`.
- No `shell=True`, cloud SDK import, cloud storage dependency, public tunnel, or
  upload path was found or introduced.
- Provider URLs are validated at the selected composition boundary; video polling
  and downloads are pinned to contractual OpenRouter paths.
- HTTP redirects are disabled, provider response sizes are bounded, internally
  owned clients ignore environment proxy configuration, and cancellation is
  re-raised.
- Durable serializers reject duplicate keys and non-finite numbers. Secrets,
  Authorization headers, signed publication URLs, raw binary bytes, and raw
  provider bodies are not persisted in the audited manifests.
- Workspace confinement, symlink/hard-link checks, atomic replace, and safe
  temporary-file creation remain in place.

## 11. Recovery and idempotency posture

The stage runtime checkpoints execution before provider work. A restarted
billable video attempt with no recoverable remote identity becomes `UNCERTAIN` and
requires user action; it is not submitted as a fresh request. Remote-job creation
is durable, polling checkpoints use CAS, immutable request identity cannot change,
poll counters/timestamps cannot move backward, and terminal remote records cannot
mutate.

OpenRouter video submission remains exactly one POST attempt and is never
automatically retried. Planning artifacts are now write-once at their durable
idempotency location, matching the later planning contexts. Cancellation remains
propagated rather than converted into retry state.

## 12. Configuration defaults

The audited defaults are:

- planning: `simulated`;
- scripting: `simulated`;
- scene planning: `simulated`;
- visual-asset planning: `simulated`;
- image acquisition: `simulated`;
- video clip generation: `simulated`;
- billable video requests: `false`;
- OpenRouter video frame publisher: `disabled`;
- asset publisher: `null`;
- all production provider API keys: unset.

Normal startup therefore requires no API key, cloud credential, public URL, or
network connection. Invalid selected provider URLs and incompatible billable
combinations fail closed with typed configuration errors.

## 13. Test and quality-gate results

| Gate | Result |
| --- | --- |
| Production unit | 1,152 passed, 4 skipped |
| Characterization | 4 passed, 2 expected failures |
| Golden | 1 passed |
| Integration | 54 passed, 1 skipped |
| Stress | 9 passed |
| Full repository | 1,242 passed, 5 skipped, 2 expected failures |
| MyPy `backend/src/production` | Passed, 254 source files |
| Ruff, production + production unit tests | Passed |
| `git diff --check` | Passed |

Two repository-wide Ruff conditions were verified as pre-existing and outside the
bounded Phase 5I change policy. At baseline, `ruff format --check backend`
reported 371 files and `ruff check backend` reported 206 violations. The final
run reports 343 files and 203 violations; Phase 5I did not add a violation. The
first issue remains
`backend/src/agents/director_agent/application/director_service.py:4` (`F401`).
No broad ignore or configuration weakening was added.

## 14. Recommended next phases

1. Preserve this architecture gate as audio contracts are introduced.
2. Add speech/audio planning first as a new bounded context with simulated,
   offline defaults and its own durable contracts.
3. Plan the container-construction transaction and settings side-effect cleanup
   before resource-heavy editor integration.
4. Keep DaVinci and frontend work separate from provider and durable-contract
   changes.

## 15. Live-request confirmation

Phase 5I made no live OpenRouter request, no provider-discovery request, no cloud
request, no upload, and no billable request. No API key, funded account, public
URL, tunnel, DaVinci installation, or external infrastructure was used.
