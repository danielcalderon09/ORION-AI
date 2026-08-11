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
