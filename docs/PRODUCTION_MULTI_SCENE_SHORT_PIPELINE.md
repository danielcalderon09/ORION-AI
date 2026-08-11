# Duration-aware multi-scene short pipeline — Phases 7A–7B.3

## Phase 7B.2 audio-first resolution

The requested target is an editorial intention, while durable narration establishes
the resolved media duration within a fail-closed tolerance. Each scene resolves to
`max(planned_duration_ms, narration_duration_ms)`; later scenes shift without gaps or
overlaps. Video longer than its resolved slot is trimmed, and video shorter than its
slot freezes its last frame. Video is never replayed from the beginning.

The default extension ceiling is the smaller of 3,000 ms and 20 percent of the
requested target. These values are configurable through
`ORION_MEDIA_COMPOSITION_MAXIMUM_ABSOLUTE_EXTENSION_MS` and
`ORION_MEDIA_COMPOSITION_MAXIMUM_RELATIVE_EXTENSION_RATIO`.

The duration resolution is persisted in the completed speech-generation manifest.
It records the requested target, per-scene planned/narration/resolved durations,
the resolved total, the configured maximum, and whether the result was accepted.
Older speech manifests without this optional record remain readable.

## Phase 7B.3 stage order

The production order is:

`SCRIPT -> INITIAL SCENE SEMANTICS -> TTS -> RESOLVE DURATIONS -> FINAL SHOT EXPANSION -> VISUAL PLAN -> IMAGES -> VIDEO -> SUBTITLES -> TIMELINE`

TTS remains one narration WAV per scene. Once every WAV is stored, ORION measures
its durable duration and resolves the complete batch. An excessive resolution fails
with `duration_resolution_invalid`; no video request can reach PREPARED or POST.
Video generation reads the accepted durable record by `scene_id`, selects the
smallest discovered provider duration that covers each resolved scene, and performs
request-count, source-provenance, per-request cost, and aggregate-job cost checks
before the first video submission.

Planned duration is the editorial allocation. Resolved duration is the natural
narration duration reconciled with that allocation. Provider purchased duration is
the discrete capability duration selected to cover the resolved scene and trimmed
locally to its slot.

For the 8-second reference, narration resolves to 4.25 s and 5.00 s. Capabilities
`(4, 6, 8)` select 6 s and 6 s. At USD 0.03/s, the aggregate estimate is USD 0.36.
A USD 0.25 job ceiling rejects the batch before the first POST; authorization is
never increased automatically.

## Phase 7B.4 narration fitting loop

When measured narration exceeds the existing duration tolerance, ORION now keeps
video blocked and performs a bounded fitting loop inside `generating_narration`.
There is no additional production stage. On the first fitting round, only scenes
whose measured WAV exceeds their planned slot are revised. On a later round, ORION
selects the smallest set of largest remaining overruns that can remove the global
excess.

Each scene revision is a small structured OpenRouter request using the configured
scripting model. Its prepared/submitting/completed/failed/uncertain lifecycle,
text hashes, measured and target durations, cost authorization, safe provider
metadata, and revised text are checkpointed in the speech manifest. A completed
revision is reused after restart. Revised speech uses a new deterministic segment
identity, so the previous paid WAV remains intact while only changed scenes receive
new TTS.

The default allows at most two fitting rounds after the original narration. If
duration still exceeds the same unchanged tolerance, the stage fails permanently
with `narration_fitting_exhausted`; video has not run. Fitting, TTS, and video keep
separate cost gates. No limit is increased automatically.

The sequence is therefore:

`TTS -> MEASURE -> RESOLVE -> [FIT TEXT -> RE-TTS -> MEASURE -> RESOLVE] -> VIDEO`

### Deterministic local fitting before remote fitting

Small Spanish-language overruns first pass through a deterministic local fitter.
It applies a versioned allow-list of conservative grammatical reductions, such as
shortening verbose purpose, time, location, ability, and threshold constructions.
It never slices text, drops a sentence blindly, changes playback speed, or invents
content. Named entities, negations, and numeric expressions are checked after the
rewrite; large overruns and candidates outside the bounded retention policy are
reported as not applicable.

A successful local candidate is synthesized once and measured like any other
narration revision. If that WAV fits, no remote fitting request or fitting budget
is consumed. If the measured candidate remains too long, the existing OpenRouter
fitter remains available in the same logical fitting round and retains all of its
authorization and retry guards.

Local revisions are durable `deterministic_local` fitting records with source and
candidate hashes, target duration, rules applied, and a deterministic fingerprint.
They carry zero provider cost, no provider request ID, and no HTTP lifecycle. A
completed local record and its revised WAV are reused during recovery. Historical
OpenRouter records without strategy fields keep their original serialized shape
and fingerprint behavior.

The effective order is:

`TTS -> MEASURE -> LOCAL FIT -> RE-TTS -> MEASURE -> [REMOTE FIT FALLBACK] -> VIDEO`

OpenRouter fitting is disabled and non-billable in committed defaults. Activation
requires explicit per-attempt and aggregate-job cost authorization through the
`ORION_NARRATION_FITTING_*` settings. Because revised scenes create additional TTS
requests, the speech request-count and aggregate speech-cost limits must also cover
the explicitly authorized worst case.

Within one fitting attempt, the OpenRouter adapter permits at most one additional
provider call, and only for a timeout, connection failure, HTTP 429, or HTTP 5xx.
HTTP 4xx, authentication, invalid JSON, structured-output, and semantic revision
failures are terminal for that attempt. Durable fitting records retain the safe
category, retryable flag, HTTP status, provider request ID, response/header receipt,
and provider retry count; secrets and raw provider bodies are never persisted.

### Explicit fitting recovery authorization

Increasing `ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD` does not silently
reauthorize historical jobs. Existing fitting records retain their original
estimated exposure, reported cost, request identity, terminal status, and
authorization. An operator must first raise the current Settings ceiling and then
persist a separate recovery authorization:

```text
python -m backend.src.production.cli.authorize_narration_fitting_recovery \
  --job-id <job-id> \
  --maximum-job-cost-usd 0.004
```

This local operation validates the failed narration stage, the compatible fitting
failure, committed estimated exposure, current Settings ceiling, provider-retry
policy, and source-manifest SHA-256. It creates one immutable, fingerprinted
`narration-fitting-recovery-authorization.json` sidecar and never calls a provider.
Repeating the exact command is idempotent; a different authorization or source
manifest drift fails closed.

On the next explicit stage retry, ORION creates a new speech stage attempt from the
hash-pinned historical manifest. Completed scene fitting and original WAVs are
reused. A failed logical fitting attempt remains immutable, while the next fitting
attempt receives a new durable identity. Estimated authorization reservations stay
separate from lower reported provider cost.

If that recovery attempt also exhausts the fitting policy, a later operator action
may authorize the next logical round. The latest failed speech manifest becomes the
new pinned source, and the authorization is written as
`narration-fitting-recovery-authorization-attempt-3.json` (or the corresponding
target attempt). The original legacy sidecar and all earlier manifests remain
readable and immutable. The command still refuses the operation unless the current
`ORION_NARRATION_FITTING_MAX_ATTEMPTS` permits the next round.

Phase 7A validates ORION's first 2–5 scene short-form architecture completely
offline. Simulated providers and `httpx.MockTransport` remain the test boundary;
the feature does not activate billable providers.

## Durable scene identity

Every scene keeps `scene_id`, sequence/scene number, planned start, planned end,
and planned duration. Images and clips remain joined through durable scene,
visual-asset, source-image artifact, and shot identities. Manifest ordering uses
scene and shot order rather than UUID lexical order.

## Duration allocation and provider selection

The pure allocator receives the target duration, scene identities, and narration
word weights. It applies a minimum scene duration, distributes remaining
milliseconds by largest remainder, creates no gaps or overlaps, and makes the
last scene end exactly at the target.

Video duration selection uses discovered `supported_durations` and chooses the
smallest value that covers the scene. With `(4, 6, 8)`, `3→4`, `4→4`, `4.1→6`,
`6→6`, and `7.9→8`; a scene above the provider maximum fails before submission.
Phase 7A does not split a scene into multiple paid generations.

## Aggregate video budget and source preflight

The existing per-request ceiling remains mandatory. Before the first new video
submission, ORION estimates every known pending scene and compares the sum with
`ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD`. Request-count and
aggregate-cost failures block the batch before `POST /api/v1/videos`.

All first frames are checked as a batch first. If any source carries a durable
simulated marker, all new video submissions are rejected with
`simulated_source_asset_not_billable`; an earlier scene is not paid before a
detectable later-source failure.

## Narration, subtitles, and timeline

Speech remains one narration WAV per scene. Subtitle generation uses durable
measured speech durations when available. Composition defines actual scene
duration as the maximum of planned duration and required narration duration,
then shifts every later scene, narration segment, subtitle cue, and sound-effect
offset. Longer video is trimmed; shorter legacy video freezes its final frame and
is never replayed from the beginning. Narration normally plays at rate 1.0.

Video manifests retain planned duration, selected provider duration, actual
duration, and `video_adaptation` (`none`, `trim`, or `freeze`).

## Recovery and offline verification

Each visual keeps an independent durable remote identity. Stored scene clips are
reused on resume, and timeline/render failures reuse all completed remote media.

- A 15-second, three-scene simulated E2E produces three images, three clips,
  three narration assets, subtitles, a timeline, MP4, and final validation.
- Mock capabilities `(4, 6, 8)` select `(4, 6, 6)` for planned `(4, 5, 6)`.
- At USD 0.03/s the aggregate estimate is USD 0.48; a USD 0.40 job ceiling
  rejects the batch before the first POST.

## Roadmap

- Phase 7A: offline duration-aware multi-scene pipeline.
- Phase 7B: controlled two-scene real test.
- Phase 7C: three-to-five-scene production short.

## Duration and provider purchase planner

`planning.provider_budget_planner` separates three decisions that must remain
auditable before any video request:

1. `EditorialDurationPlan`: requested duration, adaptive scene count, narrative
   role, and contiguous editorial targets.
2. `AudioFirstNarrativePlan`: actual per-scene narration and resolved media
   duration, using the canonical audio-first tolerance policy.
3. `VideoProviderPurchasePlan`: visual clips required to cover each resolved
   narrative scene, purchased provider seconds, per-scene cost, and aggregate
   job cost.

Narrative scenes are not required to equal visual clips. A long scene can use
multiple provider clips. Coverage is selected deterministically from discovered
provider durations: purchased seconds are minimized first, then request count.
For example, 9 seconds with `(4, 6, 8)` becomes `6 + 4`, 14 seconds becomes
`8 + 6`, and 16 seconds becomes `8 + 8`. Each clip is trimmed when its purchased
duration exceeds its usable slot; no loop or replay mode is planned.

The entire purchase plan is built before submission. `accepted` is false when
total clip count exceeds the job request limit, any individual clip exceeds its
per-request cost ceiling, or aggregate estimated cost exceeds job authorization.
The caller must authorize the plan before creating provider requests, so a later
scene cannot discover a budget failure after an earlier paid submission.

For the OpenRouter runtime, the accepted plan is embedded immutably in the
video-clip manifest and checkpointed before the first `/api/v1/videos` POST.
Its canonical SHA-256 fingerprint binds provider/model, source-image hashes,
scene/shot/clip identities, usable durations, purchased durations, costs, and
authorization limits. Every provider request must match the corresponding plan
clip exactly. Resume loads the previous attempt's plan, reconstructs it from the
same durable inputs, and fails closed before submission if the fingerprint
drifts.

Multi-clip narrative scenes require distinct post-TTS visual shots. Each shot
has its own objective, prompt, source image, durable ID, and usable interval,
while sharing the scene StoryBeat and VideoIdentity. Runtime planning never
duplicates a first frame to manufacture an extra clip: the durable expansion creates
the required shot split before final visual planning and image acquisition. Timeline
composition consumes those shots contiguously with
`playback_mode=once` and `loop_count=1`.

The planner is provider-neutral and includes future `generation_mode` values for
full AI video, hybrid, image motion, and stock. The economy selector is not
implemented yet; a future policy can choose Veo only for high-impact scenes and
local image motion for explanatory scenes without changing these contracts.

## Post-TTS durable shot expansion

Final visual shots are no longer purchased or imaged before narration timing is
known. The completed speech manifest is measured first. Visual asset planning then
derives and atomically persists `shot-expansion.json`, binding the source ScenePlan,
speech-manifest identity, resolved scene durations, provider-duration vocabulary,
shot allocation, distinct shot semantics, and expanded visual ScenePlan under one
canonical SHA-256 fingerprint.

For a 9-second narrative scene and provider durations `(4, 6, 8)`, the expansion
creates two deterministic shots: 6,000 ms and 3,000 ms usable time. Their provider
purchases are 6 s and 4 s. Each shot has a distinct camera/composition progression,
its own VisualAssetSpec, its own SOURCE_IMAGE, and its own first frame. Image
acquisition cannot start until the final visual plan referencing the expansion is
durable.

Recovery reconstructs the expansion from the durable ScenePlan and accepted speech
manifest. Any narration, allocation, or fingerprint drift fails closed before new
image or video requests. Existing single-shot visual plans without expansion
provenance remain readable and reusable. Composition uses the immutable purchase
plan's usable shot intervals when present, preserving contiguous scene coverage,
`playback_mode=once`, and `loop_count=1`.

## Production Short V1 visual strategy contract

Post-TTS visual shots now distinguish editorial duration from provider purchase
duration. Every shot always owns `usable_duration_ms`, while
`provider_duration_seconds` is present only for generated video. The provider-neutral
visual modes are `generated_video`, `generated_image`, `reused_video`, and
`reused_image`; image shots can also declare static, pan, zoom-in, zoom-out, or
combined pan-and-zoom intent. Reused modes require a durable `source_asset_id` and
never carry provider purchase duration.

Bounded importance and generation-priority enums prepare deterministic hybrid
selection without accepting arbitrary scores from a model. Phase SHORT-V1.1 keeps
the runtime in `LegacyFullVideoStrategy`: every newly expanded shot remains generated
video with static local motion. Legacy defaults are omitted from canonical JSON, so
historical shot-expansion payloads and fingerprints retain their exact serialized
shape. Image-motion rendering, runtime reuse, and hybrid spending are intentionally
not enabled in this phase.

### SHORT-V1.2 hybrid strategy and aggregate exposure

The pure visual strategy planner canonicalizes post-TTS shots and supports three
durable policies: `full_video`, `hybrid_balanced`, and `hybrid_economy`. Selection
uses bounded importance, generation priority, narrative role, shot function, and
stable scene/shot identity. Balanced planning targets half of the visual shots and
spreads video moments across the timeline; economy targets roughly thirty percent
while retaining the hook and another reveal/payoff when available. A model can
describe a shot but cannot authorize spending.

Generated image shots receive deterministic pan or zoom intent only; no FFmpeg
image-motion path is enabled yet. Reuse is preserved only when the input already
contains an eligible `source_asset_id`. The planner never invents a reusable source.

Before future asset acquisition, `AggregateVisualBudgetPlan` counts one image for
each generated image and one first-frame image for each generated video. That
first-frame requirement is a single image request, not an additional generated-image
charge. Only `generated_video` shots create video requirements. The plan applies
image request/cost limits, video request/per-request/job limits, and an independent
maximum total visual cost. Every plan is versioned, canonically serialized, and
fingerprinted; rejection is a pure fail-closed result and makes no provider call.

SHORT-V1.2 does not alter StageRegistry or production handlers. Runtime remains
legacy full-video until image acquisition, video generation, and composition gain
explicit hybrid support in later phases.

### SHORT-V1.3 hybrid asset acquisition boundary

Hybrid image acquisition consumes the already selected `HybridVisualStrategyPlan`
and its authorized `AggregateVisualBudgetPlan`; it never selects a strategy or
recounts requests inside the acquisition loop. A canonical acquisition identity
binds the final visual intent, strategy fingerprint, budget fingerprint, shot ID,
asset ID, visual mode, motion intent, usable duration, and schema version. Any
source or fingerprint drift fails closed before another image request.

`generated_video` produces exactly one durable first-frame image and
`generated_image` produces exactly one durable final image. Both use the single
corresponding image requirement from the aggregate plan. `reused_image` and
`reused_video` produce no image-provider request: their declared `source_asset_id`
must resolve through an allowed durable catalog with matching media type and stable
integrity metadata. A missing source or changed SHA-256 fails closed.

The hybrid acquisition manifest records per-shot visual mode, motion intent,
usable duration, origin, generated/reused status, durable asset identity, SHA-256,
MIME type, dimensions where applicable, provenance, and both upstream
fingerprints. Checkpoint recovery reuses completed generated images and resolved
catalog references, while preserving deterministic request identities. The legacy
`ImageAcquisitionHandler` remains unchanged and is still the active production
path; container registration, hybrid video consumption, image-motion rendering,
and real reuse are deferred to subsequent phases.

### SHORT-V1.4 deterministic local image motion

A separate versioned hybrid composition plan now represents both video and image
segments without changing the historical full-video schema. Image duration comes
only from the editorial shot interval; still images have no source-media duration
and no provider-video purchase identity. Video segments retain
`playback_mode=once` and `loop_count=1`.

The local FFmpeg adapter supports static images, deterministic four-direction pan,
zoom-in, zoom-out, and restrained combined pan-and-zoom. A durable motion plan pins
source SHA-256, mode, direction, start/end scale and position, exact frame count,
output geometry, strategy fingerprint, and hybrid-acquisition fingerprint. The
same source and shot identity always produce the same motion. Scale-to-cover plus
center crop preserves aspect ratio without stretching or black borders.

FFmpeg's still-image input loop is an implementation detail used to synthesize
frames from one image; it is not video replay and never emits `-stream_loop`.
Every segment is trimmed to its frame-derived editorial duration and concatenated
with safe CUT boundaries, including video-to-image, image-to-video, and
image-to-image sequences. Before execution, local files must exist and match their
durable size, SHA-256, MIME family, and image dimensions. Plan, source, geometry,
duration, motion, strategy, or acquisition drift fails closed before FFmpeg.

The active production container is unchanged in SHORT-V1.4. Narration, music,
subtitles, the audio-first ordering, and historical rendering remain on their
existing paths until the reviewed hybrid runtime integration phase.

### SHORT-V1.5 hybrid video-generation boundary

The offline hybrid video boundary consumes the durable visual strategy, aggregate
visual budget, and completed hybrid acquisition manifest. It does not select a
strategy or recalculate purchases. Before the first provider call it pins and
validates the strategy, budget, acquisition manifest, video requirement, provider
duration, and first-frame SHA-256.

Only `GENERATED_VIDEO` entries receive a provider request. `GENERATED_IMAGE` and
`REUSED_IMAGE` remain image references, while `REUSED_VIDEO` becomes a durable
video reference with zero provider cost and no remote generation identity. The
provider duration is still the purchased 4/6/8-second duration; editorial duration
remains independent for local trim and composition without playback loops.

The hybrid manifest is strict, versioned, deterministic, and fingerprinted.
Completed entries are immutable, transient failures resume selectively, and
uncertain submissions fail closed pending reconciliation. This boundary is not
registered in the production container or stage registry, so existing full-video
jobs continue through the legacy handler unchanged.
## SHORT-V1.6 hybrid production runtime

Hybrid execution is explicitly opt-in through `ORION_VISUAL_STRATEGY`. Its accepted
values are `full_video`, `hybrid_balanced`, and `hybrid_economy`; the default remains
`full_video`, so existing jobs and configurations continue through the historical
handlers and manifests.

For either hybrid strategy, final visual planning remains post-TTS and audio-first.
The `visual_asset_planning` stage additionally persists the immutable
`HybridVisualStrategyPlan` and `AggregateVisualBudgetPlan`. The aggregate image,
video, per-request, and total-visual limits must all pass before `acquiring_assets`;
a rejection cannot reach either media provider.

The existing stage registry is intentionally unchanged. Strategy and budget are pure
decisions within final visual planning, hybrid acquisition remains the realization of
`acquiring_assets`, and hybrid clip generation/local visual realization remains the
work of `generating_video_clips`. The latter invokes the video provider only for
`GENERATED_VIDEO`, builds a canonical `HybridImageMotionCompositionPlan`, and renders
one deterministic local visual track. `building_timeline` uses editorial shot timing
and source offsets into that track, while the established renderer continues to mux
narration, simulated music, and subtitles into the final H.264/AAC output.

Every hybrid sidecar is versioned and fingerprinted. Retry reads the latest prior
checkpoint, reuses completed images and videos, and can rerun only local FFmpeg after
a render interruption. A changed expansion, strategy, budget, acquisition manifest,
video manifest, composition plan, source checksum, or execution plan fails closed.
Historical jobs without hybrid artifacts continue to select the legacy full-video
source reader and retain their historical serialization and fingerprints.

## SHORT-V1.8 durable image provider telemetry

Hybrid image acquisition checkpoints persist sanitized evidence for each actual
provider submission. Records include the shot, image purpose (`video_first_frame`
or `image_visual`), provider/model, HTTP status, provider request ID when supplied,
timestamps, retry number, terminal status, and the stored artifact checksum.

Money is represented with `Decimal`. A provider-reported cost is accounted as
`reported`; when no reported cost is available, ORION accounts the configured
estimate as `estimated_fallback`. The durable acquisition accounting keeps the
estimated total, reported subtotal, accounted total, and counts for each cost
source separate. Consequently, a missing provider cost is never treated as zero.

Recovery retains completed entries and historical failed/uncertain provider
attempts. Only a new submission creates a new telemetry record, so completed
requests are not double-counted. Uncertain submission remains fail-closed and
cannot be retried automatically. Older acquisition manifests without telemetry
fields remain readable and retain their original fingerprints.

## SHORT-V1.9 durable total job cost accounting

ORION derives a strict, versioned, fingerprinted `ProductionJobCostSummary` from
existing durable provider sidecars. It does not persist a second mutable cost
artifact: scripting requests, remote speech jobs, narration fitting attempts,
hybrid image telemetry, historical image estimates, and remote video jobs remain
the source of truth. This keeps completed and failed jobs auditable offline without
creating reconciliation drift.

Each real provider submission is identified by its durable request fingerprint and
attempt identity. Recovery copies are deduplicated, while an actual retry remains a
separate cost record. Local deterministic fitting and reused media create no remote
request or provider cost. Missing reported cost uses the authorized estimate as an
explicit `estimated_fallback`; it is never interpreted as zero.

The summary exposes estimated, reported, fallback, and accounted subtotals for
scripting, TTS, fitting, images, and video, plus request-count coverage and a
fully-reported flag. It also audits accounted image/video cost against the immutable
aggregate visual budget. Local MVP output includes a backward-compatible optional
`cost_summary` with per-stage accounted cost and total coverage fields. No API keys,
headers, signed URLs, or provider bodies participate in accounting.

## SHORT-V1.10C uncertain TTS submission resolution

When a billable remote speech submission begins but ORION cannot durably determine
whether the provider accepted it, the remote speech record becomes `uncertain` and
`fresh_submission_permitted` remains false. This is a fail-closed terminal checkpoint:
the normal recovery command never resends that request automatically.

An explicit, provider-neutral `SpeechSubmissionResolution` sidecar can be written
only after validating the job, attempt, scene, segment, and exact request fingerprint
against the uncertain remote record and speech manifest. Supported resolutions are
`confirmed_completed`, `confirmed_not_submitted`, `confirmed_failed`, and
`unresolved`. The sidecar is versioned, canonical, fingerprinted, write-once, and
idempotent; a conflicting second resolution fails closed. It stores bounded evidence
identities only, never credentials, headers, signed URLs, or raw provider bodies.

Resolution is deliberately separate from resubmission. A `confirmed_not_submitted`
resolution requires explicit operator acknowledgement and only makes a future fresh
request eligible; it does not submit anything. A future recovery operation must create
a new request identity and new accounting entry. `confirmed_completed` is recoverable
without resubmission, while `confirmed_failed` and `unresolved` remain blocked until
the applicable recovery policy is explicitly satisfied. Historical uncertain cost
exposure remains counted once, so resolution cannot double-count or erase it.

## SHORT-V1.10E risk-authorized unresolved TTS replacement

`confirmed_not_submitted` and `unresolved` remain intentionally different. The
former is an evidence-backed statement that the provider did not receive the
request. The latter says that the outcome is still unknown and, by default, keeps
every fresh submission blocked.

For an `unresolved` record, an operator may create a separate
`SpeechUnresolvedReplacementAuthorization`. The authorization pins the job, source
and target attempts, scene, segment, original request fingerprint and record hash,
the unresolved resolution fingerprint, one additional request, a Decimal cost
ceiling, operator identity, timestamp, and explicit acceptance of possible duplicate
charges. Creating it is offline and never calls the provider.

The authorization is write-once, idempotent for the same decision, and consumed
before the single replacement submission can proceed. Consumption gives the new
submission a distinct durable identity. A second submission is blocked; if the
replacement itself becomes uncertain, another explicit authorization is required.
Completed narration segments remain reusable and normal pending segments proceed
only after the authorized replacement completes. The original uncertain record and
its estimated fallback exposure remain immutable, while any replacement cost is
accounted independently.
