# Durable Final Render Validation

Phase 5H.5 turns the existing serialized `VALIDATING_RENDER` stage into the
definitive acceptance gate for a real local render. The stage is independent
of rendering: it consumes the READY `LONG_FORM_RENDER` and its durable
provenance, never invokes FFmpeg, and never changes the MP4.

The production composition root enables this handler only when the explicitly
configured renderer is `ffmpeg`. The existing dry-run validation placeholder
is preserved, because dry-run intentionally produces no media to accept.

## Verified provenance and media

Before probing, the source reader independently verifies the registered local
render request, FFmpeg execution plan, render execution manifest, media
composition plan, and media composition manifest. Every artifact must belong
to the job, be READY, remain workspace-confined, and match its registered size
and SHA-256. Supported schema versions and the request, plan, timeline,
execution-plan, render-manifest, and output-artifact identities must form one
consistent chain.

The MP4 is read again to establish its current positive size and SHA-256. It is
then inspected through the controlled FFprobe runner already authorized by
Phase 5H.4. The check requires exactly one H.264/yuv420p video stream, AAC
audio, the expected subtitle-stream count, no unexpected streams, MP4 format,
planned dimensions, frame rate and duration within the execution policy's
durable tolerances. No prior probe summary is trusted on its own.

## Final manifest

The only new artifact type is `FINAL_RENDER_VALIDATION`. Schema `1.0.0` is
stored canonically at:

`production/<job-id>/validating_render/attempt-<n>/final-render-validation.json`

It records the job and stage, source artifact identities, current render
checksum and size, validation timestamp, normalized FFprobe summary,
validation result, warnings and diagnostic codes, request/plan/timeline/
execution/render/probe fingerprints, and one deterministic validation
fingerprint. Timestamps are not fingerprint inputs.

Lifecycle is `PREPARED -> VALIDATING -> VALIDATED`, or a terminal `FAILED`.
Atomic write-once creation and compare-and-swap checkpoints prevent silent
replacement. Canonical JSON rejects duplicate keys, non-finite values,
unsupported schemas, corrupt fingerprints, traversal and unsafe links.

## Completion, failure, and recovery

A `VALIDATED` result emits only `FINAL_RENDER_VALIDATION`. With the default
pipeline policy, the existing orchestrator advances `VALIDATING_RENDER` to the
serialized `COMPLETED` stage and marks the job `COMPLETED`. The optional,
pre-existing clip-handoff policy keeps its established stage order.

Any missing, changed, corrupt, incompatible, or probe-invalid input produces a
durable failed validation and a permanent stage failure. The original render
is preserved. No deletion, repair, promotion, transcoding, or overwrite occurs.

Recovery is deterministic:

- missing final state starts from the existing render and validates only it;
- `PREPARED` or interrupted `VALIDATING` state resumes the FFprobe check;
- a matching `VALIDATED` manifest rechecks current size/SHA-256 but does not
  invoke FFprobe or rewrite the manifest;
- a render changed after acceptance is reported as an inconsistency and is
  preserved;
- a terminal failed attempt remains inspectable and can be retried through the
  production pipeline's normal new-attempt policy.

Two successful executions over the same attempt return the same artifact and
leave identical manifest bytes and fingerprints. Existing media-composition,
render request, execution-plan, and render-execution schemas are read but
never rewritten.

## Security and limits

The final-validation context has no FFmpeg execution port. Its only media
operation is FFprobe through the controlled argument-vector runner from Phase
5H.4, with `shell=False`, disabled stdin, timeout and bounded diagnostics. It
does not use PowerShell, CMD, a shell, network, cloud, API keys, credentials,
DaVinci Resolve, MCP, dynamic plugins, downloaded assets, or remote media.

Configuration adds only
`ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES` (default 4,000,000). The
durable execution plan continues to own output-size, duration-tolerance and
frame-rate-tolerance policy.
