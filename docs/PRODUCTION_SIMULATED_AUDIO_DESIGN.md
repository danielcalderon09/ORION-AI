# ORION Durable Simulated Audio Design

## Scope

Phase 5H.1 gives `PREPARING_MUSIC` a durable, provider-neutral audio-design
implementation. It can create one background-music bed and zero or more generic
sound-effect placeholders. It does not mix tracks, render video, invoke FFmpeg
or DaVinci Resolve, or connect to a real provider.

The generated WAV files are synthetic test material. They are not
production-quality music or sound design, do not imitate an artist or existing
recording, and contain no downloaded, stock, embedded, or copyrighted samples.

## Stage placement and boundaries

The existing serialized stage `preparing_music` is reused:

`generating_narration -> preparing_music -> generating_subtitles`

Its name and position therefore remain readable for existing jobs. The former
generic no-op music handler remains available as a compatibility fallback in
the handler factory; the production composition root now injects the durable
audio-design handler.

The bounded context lives in `backend/src/production/audio_design/`. Core
contracts, plan derivation, fingerprints, duration rules, WAV validation, and
provider ports do not depend on runtime, HTTP, provider SDKs, cloud storage,
FFmpeg, DaVinci, or database implementations. Music and sound effects have
separate ports and separate stores. Concrete simulators are selected only by
composition.

## Plan derivation

Audio requirements come only from explicit, strict `ProductionScript`
metadata:

- Music: `script.metadata.audio_design.music`
- Sound effects: `scene.metadata.audio_design.sound_effects`

Music is absent unless `music.enabled` is explicitly `true`. No SFX is created
unless a scene contains an explicit sound-effect entry. Narration, visual
prompts, camera directions, and arbitrary prose are never mined for cues.

The music vocabulary is `neutral`, `calm`, `hopeful`, `focused`, `warm`, and
`subdued`. Music duration uses the script's declared total duration and must
fall inside the configured bounds.

The closed SFX vocabulary is:

- `transition`
- `impact`
- `rise`
- `alert`
- `ambience`
- `whoosh`
- `soft_click`

Unknown cues fail explicitly. Each cue has a documented deterministic default
duration: 500, 350, 1000, 400, 2000, 600, and 120 milliseconds respectively.
An explicit valid duration may override that default. Scene order and cue order
determine stable offsets and identities.

The plan fingerprint binds the source artifact ID and SHA-256, total duration,
normalized requirements, provider-neutral audio policy, and schema version.
Safe closed vocabularies prevent artist names or style-imitation instructions
from entering durable metadata.

## Deterministic synthesis

Both simulators use only Python's standard library and integer arithmetic.
They use no wall clock, OS randomness, global random state, external samples,
native synthesis library, subprocess, or network access.

Music combines several bounded triangle-wave harmonics into a repeating chord
and rhythm pattern with deterministic modulation and an envelope for
non-loopable beds. Its request fingerprint selects bounded synthesis
parameters. SFX use cue-specific tonal sweeps, pulse patterns, envelopes, a
small deterministic local noise generator for ambience, or a short alternating
click. They are intentionally generic and distinguishable from narration.

An identical logical request produces byte-identical output. A relevant change
to mood, intensity, duration, loopability, cue type, PCM policy, provider
version, or schema changes the canonical SHA-256 request fingerprint and output.
Attempt numbers, timestamps, and paths are not fingerprint inputs.

## WAV contract and duration safety

Every output is:

- RIFF/WAVE
- uncompressed PCM
- mono
- 24,000 Hz
- 16-bit little-endian samples
- `audio/wav` with `.wav`

Frame count is integer half-up rounding:

`frames = (duration_ms * sample_rate_hz + 500) // 1000`

The validator requires a canonical 44-byte PCM header, exact RIFF and data
sizes, supported metadata, exact frame count, non-empty and non-silent frames,
bounded peak amplitude, no clipping, no trailing bytes, and the expected
SHA-256. Default limits are 1–180 seconds for music, 50 milliseconds–5 seconds
for SFX, and 10,000,000 bytes per WAV. Configuration rejects a maximum duration
that cannot fit inside the byte ceiling.

## Storage and manifest

Audio-design storage is context-owned and does not alter image, video, or
speech schemas:

- `production/{job_id}/assets/music/{requirement_id}-{fingerprint16}.wav`
- `production/{job_id}/assets/sound-effects/{requirement_id}-{fingerprint16}.wav`

Each WAV has a canonical `.asset.json` sidecar that records safe provenance,
checksum, size, and PCM metadata. Filenames contain only validated deterministic
identities, never prompt text.

Writes are workspace-confined, atomic, fsynced, write-once, bounded, and
idempotent for identical content. Path traversal, unsafe links, hard links,
mismatched overwrites, corrupt bytes, or conflicting sidecars fail closed.

The durable manifest is:

`production/{job_id}/preparing_music/attempt-{attempt_number}/audio-design-manifest.json`

Schema `1.0.0` uses canonical sorted JSON, strict UTF-8, duplicate-key
rejection, NaN/Infinity rejection, bounded reads, atomic replacement, directory
fsync, lock files, and compare-and-swap checkpoints. Manifest states are
`prepared`, `generating`, `complete`, and `failed`; entry states are `pending`,
`generating`, `stored`, and `failed`. Raw WAV bytes, arbitrary prompts,
credentials, endpoints, headers, signed URLs, and absolute paths are excluded.

Artifacts expose the manifest, background music when present, and each sound
effect using stable relative paths and checksums. No database JSON field stores
audio bytes.

## Recovery and idempotency

The handler checkpoints `generating` before each provider call and persists
one validated asset at a time. It then checkpoints that entry as `stored`.
Repeated stage delivery verifies and reuses stored assets.

Recovery behavior is explicit:

- no manifest: derive the plan and create one;
- prepared or partial manifest: generate only missing entries;
- verified stored entry: preserve it;
- stored entry with a missing file: mark it incomplete and regenerate
  deterministically;
- WAV written before its manifest checkpoint: verify its deterministic path,
  checksum, sidecar provenance, and PCM metadata, then adopt it without another
  provider call;
- mismatched bytes, sidecar, checksum, metadata, source fingerprint, plan
  fingerprint, configuration fingerprint, or provider identity: fail closed;
- complete and verified manifest: return completion without generation;
- unsupported schema: fail explicitly;
- cancellation: propagate cancellation and leave the pre-call checkpoint
  recoverable.

Valid completed audio is never deleted during a normal retry.

## Read-only reconciliation

`AudioDesignReconciler` does not generate, store, repair, delete, checkpoint, or
advance a job. It reports manifest presence and support, status, expected and
completed counts, missing or uncheckpointed files, orphans, corruption,
checksum and PCM metadata drift, unsafe paths, sensitive metadata, source or
plan drift, stage completeness, safe recovery eligibility, and whether manual
intervention is required.

## Configuration and composition

The active defaults are:

```text
ORION_MUSIC_GENERATION_PROVIDER=simulated
ORION_SOUND_EFFECT_GENERATION_PROVIDER=simulated
ORION_AUDIO_DESIGN_SAMPLE_RATE_HZ=24000
ORION_AUDIO_DESIGN_CHANNEL_COUNT=1
ORION_AUDIO_DESIGN_SAMPLE_WIDTH_BYTES=2
ORION_AUDIO_DESIGN_MIN_MUSIC_DURATION_MS=1000
ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS=180000
ORION_AUDIO_DESIGN_MIN_SOUND_EFFECT_DURATION_MS=50
ORION_AUDIO_DESIGN_MAX_SOUND_EFFECT_DURATION_MS=5000
ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES=10000000
ORION_AUDIO_DESIGN_MAX_MANIFEST_BYTES=4000000
ORION_AUDIO_DESIGN_MAX_SCRIPT_BYTES=2000000
ORION_AUDIO_DESIGN_GENERATING_STALE_AFTER_SECONDS=30
```

Both provider settings are literal `simulated` values. There is no real
adapter, dynamic provider import, API-key setting, provider URL, billable flag,
cloud setting, or remote execution route. Composition owns and idempotently
closes both simulators.

## Security and compatibility

Architecture tests prohibit network transports, subprocesses, provider SDKs,
research-code imports, speech dependencies, and concrete-provider imports from
the handler. Storage rejects traversal and link drift. Durable metadata rejects
sensitive values and internal absolute paths.

The production stage enum and ordering are unchanged. Speech manifests and WAV
generation, image schemas, asset publishing, endpoints, and visual providers
are unchanged. Existing jobs remain readable; a job with no explicit
audio-design metadata completes the stage with zero expected assets.

## Limitations and future boundary

The synthesis is a deterministic engineering placeholder. It does not provide
human-composed music, realistic Foley, loudness normalization, multichannel
audio, stems, mastering, ducking execution, timeline placement, final mixing,
or final video rendering.

A future real-provider phase must be separately researched and authorized. It
must add capability, rights, privacy, cost, and duplicate-billing controls
without changing these domain contracts to depend on a concrete vendor. Final
mixing and DaVinci integration remain separate later phases.

Phase 5H.1 used no real media, provider SDK, API key, provider URL, account,
network request, cloud operation, upload, download, billable request, FFmpeg
process, or DaVinci process.
