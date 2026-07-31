# Controlled Local FFmpeg Renderer

Phase 5H.4 adds ORION's first real final-media renderer. This remains a
personal Windows-workstation integration, not SaaS, a remote worker, public
API, plugin system, or cloud service. FFmpeg is the primary automatic
renderer; DaVinci Resolve remains disabled for a later optional local-editor
integration.

## Authorization boundary

Only `rendering/process_runner.py` may start Phase 5H.4 processes. It uses
Python's argument-vector-only `create_subprocess_exec` API (`shell=False`
semantics), disables stdin, bounds stdout/stderr, enforces a timeout, and
terminates then kills when required. It selects only the exact resolved
`ffmpeg` or `ffprobe` path. It never invokes PowerShell, CMD, scripts, DaVinci,
Resolve MCP, or arbitrary executables.

There is no network request, download, installation, remote URL, API key,
credential, billing operation, or cloud renderer.

## Activation and readiness

`ORION_RENDERER` accepts `dry_run` or `ffmpeg`; DaVinci and arbitrary values are
rejected. Explicit FFmpeg selection never falls back to dry-run.

FFmpeg and FFprobe resolve independently from a configured path or
`shutil.which`. Resolution is non-recursive and requires an absolute regular
file with the exact binary stem. Script suffixes are forbidden. Bounded
`[binary, "-version"]` probes retain only a normalized release token. Absolute
paths, banners, usernames, and machine paths are not durable identities.

## Contracts and storage

FFmpeg request and execution-manifest schema: `1.1.0`. Dry-run continues to
write/read `1.0.0` for exact Phase 5H.3 compatibility. FFmpeg execution-plan
schema: `1.0.0`.

```text
production/{job_id}/rendering/attempt-{n}/local-render-request.json
production/{job_id}/rendering/attempt-{n}/ffmpeg-execution-plan.json
production/{job_id}/rendering/attempt-{n}/render-execution-manifest.json
production/{job_id}/rendering/attempt-{n}/work/{request_prefix}/output.partial.mp4
production/{job_id}/output/orion-{job_id}-{plan_prefix}.mp4
```

The plan persists verified relative inputs and an ordered allowlisted argument
vector. Its fingerprint includes request, asset, filter graph, output,
subtitle strategy, and bounded encoding-policy identities. It excludes binary
paths, timestamps, attempts, absolute paths, prompt text, environment, user,
and machine identity.

## Composition and security

Every asset is re-resolved through workspace confinement immediately before
rendering. It must be a regular non-linked local file matching durable size,
SHA-256, MIME type, media kind, and relative path.

The baseline supports ordered video trimming/looping, cuts, concatenation,
scaling/padding, frame-rate normalization, and yuv420p; narration offsets and
gain; optional music gain/fades/ducking; optional SFX offsets/gain; and
optional durable SRT muxed as `mov_text`. Subtitle text is never invented.
Only `none` and `cut` transitions are accepted. Dissolve, fade, wipe, and
match-cut requests fail before execution.

Output is closed to MP4, libx264/H.264, AAC, and yuv420p. No user-supplied
codec, map, filter, metadata, extra argument, URL, pipe, wildcard, environment
expansion, or shell expression is accepted.

## Probe-gated promotion and artifact

FFmpeg writes only the request-owned partial MP4. The unrelated final path is
never an FFmpeg overwrite target. After exit zero, FFprobe JSON must establish
a safe non-empty file within the byte limit, exactly one H.264/yuv420p video
stream, AAC audio, expected dimensions/frame rate/duration, MP4 compatibility,
the expected subtitle count, and no unexpected stream.

Only then is SHA-256 calculated and the file atomically promoted. A READY
`LONG_FORM_RENDER` is emitted after promotion with actual size/checksum,
normalized versions, timeline identity, codecs, dimensions, duration, frame
rate, and `validated_by_ffprobe=true`. No zero-byte or pre-probe final artifact
is emitted.

## Lifecycle, recovery, and reconciliation

Dry-run keeps `PREPARED -> VALIDATING -> VALIDATED` without media. FFmpeg uses
`PREPARED -> READY_TO_RENDER -> RENDERING -> VALIDATED`, with `FAILED` and
`CANCELLED` checkpoints.

Locked compare-and-swap writes protect state. Request-only and manifest-only
preparation recover safely. Interrupted rendering never assumes an old process
still runs. A valid owned partial can be promoted; only an invalid owned
partial may be removed before restart. A validated final is reused only when
size and SHA-256 agree. Unexplained or conflicting final files are preserved
for manual intervention. Timeout, cancellation, non-zero FFmpeg exit, or
FFprobe failure emits no final artifact.

Reconciliation is read-only. It reports source/request/plan/manifest,
temporary/final output, checksum and artifact agreement, durable version/probe
state, interruption/failure/timeout, recovery safety, and manual-intervention
need. It never renders, deletes, promotes, repairs, or rewrites.

## Configuration and limitations

`.env.example` lists binary paths, preset, CRF, closed audio bitrate,
process/probe timeouts, bounded diagnostics/output, and duration/frame-rate
tolerances. There is no arbitrary argument or filter setting.

The Phase 5H.4 integration test actually rendered and FFprobe-validated a real
local MP4 from tiny generated, non-copyrighted fixtures, then proved replay did
not rerender it. This proves renderer integration, not every historical or
future full prompt-to-video job.

Current simulated upstream clips are genuine FFprobe-validated H.264 MP4 files
and are decodable when generation completed successfully. Rendering honestly
stops for incomplete, undecodable, or changed assets.

Hardware encoders, GPU discovery, alternate formats/codecs, multiple profiles,
advanced transitions, subtitle burn-in, DaVinci, remote assets, and cloud
rendering remain unsupported.

Phase 5H.5 adds a separate definitive acceptance stage documented in
`PRODUCTION_FINAL_RENDER_VALIDATION.md`. It consumes the READY render and runs
FFprobe again; it never invokes FFmpeg or modifies this renderer's manifests.
