# Duration-aware multi-scene short pipeline — Phase 7A

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
offset. Longer video is trimmed; shorter video uses the existing deterministic
loop. Music may loop only when narration extends the planned timeline.

Video manifests retain planned duration, selected provider duration, actual
duration, and `video_adaptation` (`none`, `trim`, or `loop`; `freeze` is reserved
for future implementation).

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
