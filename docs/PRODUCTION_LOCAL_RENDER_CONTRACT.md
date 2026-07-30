# ORION local render execution contract

## Scope

Phase 5H.3 introduces the smallest renderer-neutral boundary needed by ORION's
personal, local Windows workflow. ORION is not a SaaS, a public render API, a
multi-user platform, or a distributed worker system. It has no multi-tenancy,
remote renderer discovery, plugin marketplace, cloud renderer, queue, webhook,
account, credential, or billing path.

The existing serialized `rendering_long_form` stage is reused. It still follows
`building_timeline` and still advances to `validating_render`. Its historical
simulated handler emitted a READY zero-byte `LONG_FORM_RENDER`; that placeholder
is removed from production composition. The stage now emits only durable JSON
preparation artifacts and never claims that a video exists.

## Renderer boundary

`LocalRenderer` has four members:

- `renderer_kind`;
- `capabilities`;
- `prepare_or_validate(request)`;
- `close()`.

It deliberately has no submit, poll, cancellation, remote-worker, billing, or
transport contract. Construction is explicit in the local composition root.

The closed renderer identities are:

| Identity | Activation | Readiness | Phase 5H.3 behavior |
| --- | --- | --- | --- |
| `dry_run` | active | ready | validates preparation; produces no media |
| `ffmpeg` | disabled | not configured | future identity only |
| `davinci_resolve` | disabled | not configured | future identity only |

No executable discovery or machine probing occurs. Configuration accepts only
`dry_run`; knowing a future identity does not make it activatable.

## Capabilities

Capabilities state locality, whether media is produced, supported planning
features, optional size/rate bounds, and deterministic preparation. The dry-run
renderer can validate video, narration, music, sound effects, subtitles,
transitions, envelopes, ducking, fades, and vertical layouts. It sets
`produces_media=false` and advertises no actual container, video-codec, or
audio-codec output support.

The disabled future identities have conservative metadata and make no feature
guarantees. A canonical SHA-256 over the complete capability model is stored in
the execution manifest.

## Verified Phase 5H.2 input

`VerifiedMediaCompositionSourceReader` selects the single deterministic latest
READY `MEDIA_COMPOSITION_PLAN` and `MEDIA_COMPOSITION_MANIFEST` pair. It rejects
conflicting latest candidates or differing attempts. Before returning domain
contracts it verifies:

- both artifacts belong to the job and have complete registry metadata;
- paths are workspace-confined POSIX-relative paths;
- files are regular, single-link files;
- configured byte limits, exact sizes, and SHA-256 values match;
- strict composition schemas and plan fingerprints validate;
- manifest status is `complete`;
- plan path, SHA-256, size, source fingerprint, plan fingerprint, and timeline
  checksum agree;
- every asset validation is available and its actual SHA-256 matches;
- upstream artifact metadata still states `renderer_executed=false`.

The durable composition plan remains authoritative. Media bytes are never
embedded in the render request.

## `LocalRenderRequest` 1.0.0

The versioned request contains:

- stable request and job identities;
- renderer identity;
- source plan artifact ID, relative path, SHA-256, plan fingerprint, and
  timeline checksum;
- duration in milliseconds and frames;
- dimensions, rational frame rate, aspect ratio, and color space;
- renderer-relevant track counts and feature flags;
- ordered asset ID, relative path, SHA-256, fingerprint, media kind, and
  optional duration references;
- logical requested output;
- request fingerprint;
- `dry_run=true` and safe metadata.

The request ID is UUIDv5 over the request fingerprint. Attempt number,
timestamps, absolute paths, user or machine names, process IDs, temporary
locations, and executable state do not participate in identity.

The request fingerprint includes schema and renderer contract versions,
renderer kind, source plan fingerprint/SHA-256, timeline checksum, output
specification, dimensions, frame rate, duration, ordered asset
IDs/fingerprints, track summary, and the dry-run flag. The same logical plan and
output therefore produce the same request across retries.

## Requested output

The future output contract is logical only:

- container: `mp4`;
- video codec: `h264`;
- audio codec: `aac`;
- pixel format: `yuv420p`;
- MIME type: `video/mp4`;
- overwrite policy: `fail_if_exists`;
- subtitles: follows the composition plan.

The deterministic filename is
`orion-{job_id}-{plan_fingerprint_prefix_12}.mp4`, under
`production/{job_id}/output/`. Neither the file nor its output directory is
created. If that path unexpectedly exists, the handler preserves it and returns
a user-action conflict.

## Dry-run validation

`DryRunRenderer` verifies request identity, positive duration, valid dimensions
and rational frame rate, five represented composition tracks, required video
and narration, ordered unique assets with checksums and safe paths, matching
safe output filename/path, and planning-feature support.

Its deterministic result has `accepted=true`, `media_produced=false`,
`output_created=false`, validated counts, sorted validation codes, and
`deterministic=true`. This is successful render preparation, not a successful
video render.

## Durable layout and lifecycle

For render attempt `n`, storage is:

```text
production/{job_id}/rendering/attempt-{n}/local-render-request.json
production/{job_id}/rendering/attempt-{n}/render-execution-manifest.json
```

Both use canonical strict JSON. Reads reject duplicate keys, NaN/Infinity,
corrupt fingerprints, unsupported schemas, oversized content, unsafe links,
hard links, traversal, and workspace escape. The request is atomic and
write-once. The manifest uses lock-protected compare-and-swap checkpoints.
Identical writes are idempotent and conflicting content is preserved and
rejected.

Manifest schema `1.0.0` has these states:

- `prepared`;
- `validating`;
- `validated`;
- `invalid`;
- `failed`.

This phase has no `rendered` state. Successful execution ends at `validated`.
The output artifact ID, SHA-256, and byte size remain null, while
`media_produced` remains false. A manifest claiming media or an output checksum
cannot validate under this phase's strict model.

## Recovery

Recovery is deterministic:

- no state: derive and write the request, prepare the manifest, validate;
- request only: reuse it, create the manifest, validate;
- `prepared`: checkpoint `validating`, then validate;
- interrupted `validating`: safely repeat only dry-run validation;
- `validated` with matching identities: complete without another renderer call;
- changed source or request fingerprint: reject stale durable state;
- corrupt request or manifest: fail explicitly;
- conflicting durable content or CAS: preserve it and fail;
- unexpected future output: preserve it and require user action;
- false media-produced state: reject during strict manifest loading.

## Read-only reconciliation

`LocalRenderReconciler` reports source/request/manifest presence, schema and
identity validity, request fingerprint validity, renderer/readiness, dry-run
result and acceptance, media-produced state, unexpected output, stale or corrupt
state, recovery safety, manual-intervention need, and stage completeness.

It does not create directories, rewrite or repair JSON, delete output, invoke a
renderer, inspect installations, or probe executables.

## Configuration and composition

The local settings are:

```text
ORION_RENDERER=dry_run
ORION_RENDER_OUTPUT_CONTAINER=mp4
ORION_RENDER_VIDEO_CODEC=h264
ORION_RENDER_AUDIO_CODEC=aac
ORION_RENDER_PIXEL_FORMAT=yuv420p
ORION_RENDER_MAX_REQUEST_BYTES=4000000
ORION_RENDER_MAX_MANIFEST_BYTES=4000000
```

The composition root explicitly constructs the verified source reader, local
store, `DryRunRenderer`, durable handler, and read-only reconciler. The
reconciler and renderer are exposed only through the existing internal
container. There is no dynamic import or plugin discovery.

## Future local boundaries

FFmpeg is intended as the future primary automatic local renderer. A later
phase may add a controlled adapter behind the renderer boundary, with its own
execution result and validation rules. Phase 5H.3 adds no executable path,
command line, process invocation, or codec guarantee.

DaVinci Resolve is intended as an optional advanced local renderer/editor. A
later phase may integrate it behind an explicit local adapter. Phase 5H.3 adds
no Resolve scripting, API, MCP, Fusion, project, or timeline operation.

## Security and explicit limitations

No video was rendered. No MP4, MOV, or MKV was created. No frames, encoded
audio, thumbnails, proxies, or project files were generated. No encoding or
muxing occurred. No FFmpeg or FFprobe process ran. No DaVinci Resolve, Fusion,
or MCP operation ran. No renderer subprocess or shell ran. No network request
occurred. No cloud service, remote render provider, API key, credential,
account, URL, or billing setting exists in this bounded context.

The only active behavior is deterministic dry-run validation. It does not prove
that a machine can encode the requested codecs, that an executable is installed,
or that the future media output will pass post-render inspection.
