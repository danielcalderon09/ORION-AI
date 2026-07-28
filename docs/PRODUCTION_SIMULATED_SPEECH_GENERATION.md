# Production Simulated Speech Generation

## 1. Purpose

Phase 5G.1 replaces the historical zero-byte narration placeholder with a
provider-neutral, durable speech-generation bounded context. It consumes the
approved durable production script and produces technically valid PCM WAV
assets for later timeline and editor phases.

The current output is a deterministic tone-pattern placeholder. It is not a
human-quality voice.

## 2. Architecture

`speech_generation` owns:

- immutable speech requests, entries, manifests, summaries, and audio sidecars;
- deterministic narration segmentation;
- the `SpeechGenerationProvider` port;
- the standard-library simulated provider;
- strict PCM WAV inspection;
- write-once speech audio storage;
- atomic compare-and-swap manifest checkpoints;
- the `SpeechGenerationHandler`;
- read-only reconciliation.

The handler depends on speech ports. Composition selects
`SimulatedSpeechGenerationProvider`, the filesystem audio store, manifest
writer, speech-owned source adapter over the stable durable script reader, and
reconciler. The shared script reader has no reverse dependency on speech.

Speech does not import or call image providers, video providers, OpenRouter,
asset publishers, cloud SDKs, DaVinci, FFmpeg, or subprocesses.

## 3. Input script contract

The stage reads the completed `ProductionScript` through the existing durable
script adapter. That adapter verifies the registered artifact identity,
contractual relative path, size, SHA-256, strict UTF-8, duplicate-free JSON,
finite JSON numbers, schema version, and immutable script contract.

Segments come only from `ProductionScript.scenes[*].narration`. Visual prompts,
camera instructions, metadata, and provider responses are never narration
sources. The script language is used when present; the configured language is
only a fallback.

## 4. Segment identity

There is one Phase 5G.1 segment per script scene. Shot identity is therefore
unset, while the stable `scene-NNN` provenance is retained.

Text is normalized with Unicode NFC, collapsed whitespace, and trimmed edges.
The normalized text hash is SHA-256. The durable segment ID is SHA-256 over:

- source script artifact ID and SHA-256;
- source scene number;
- normalized text hash;
- requested voice and language;
- requested words per minute;
- explicit script scene duration.

The first 32 lowercase hexadecimal characters are serialized as
`segment-<digest>`. Python's process-randomized `hash()` is not used.

## 5. Manifest schema

Schema version `1.0.0` is stored at:

```text
production/{job_id}/generating_narration/attempt-{attempt_number}/
speech-generation-manifest.json
```

The manifest binds the job and attempt to the source script artifact and
checksum, configuration fingerprint, simulated provider, requested voice,
language and speaking rate, ordered entries, summary, status, and aware
timestamps.

An entry retains only the normalized narration input needed for deterministic
recovery and audit, plus source scene/optional shot provenance and durable WAV
metadata. Raw audio bytes, absolute paths, credentials, request headers,
provider bodies, and exception text are not serialized.

Serialization is canonical UTF-8 JSON with sorted keys, compact separators,
finite numbers, and one trailing newline. Reads reject duplicate keys,
NaN/Infinity, unknown fields, unsupported versions, and invalid state
invariants.

## 6. State machine

Segment transitions are:

```text
pending -> generating
generating -> stored | failed | uncertain
failed -> generating
uncertain -> generating | stored  (simulated-only deliberate recovery)
stored -> stored
```

The manifest uses `in_progress`, `completed`, `failed`, `partial`, and
`uncertain`. `completed` requires every entry to be `stored`; `partial`
requires at least one stored entry and at least one incomplete entry.

`uncertain` is not a general retry state. Phase 5G.1 may leave it only after
checking for verified local output and confirming that the configured provider
is the local deterministic simulator. A future real provider must define
provider-side recovery before such a transition can be allowed.

## 7. Simulated duration algorithm

An explicit script-scene duration is used when available. Otherwise the
estimate is:

```text
words * 60,000 / configured_words_per_minute
+ 120 ms per comma, semicolon, colon, period, exclamation mark, or question mark
```

The result is clamped between the configured minimum and maximum segment
duration. Frame count is the rounded product of duration and sample rate; the
durable duration is recalculated from the exact frame count.

## 8. WAV format

The simulator produces:

- RIFF/WAVE;
- uncompressed PCM;
- mono;
- 24,000 Hz by default;
- signed 16-bit little-endian samples;
- an audible deterministic alternating tone pattern with short silence gates.

The waveform frequencies and amplitude derive from the normalized text hash,
so identical input produces identical bytes and changed narration changes the
waveform. The provider uses only Python standard-library modules.

Before storage, validation checks RIFF size boundaries, WAVE identity, PCM
compression, sample rate, channels, sample width, exact frame count, exact
duration, complete non-empty frame data, absence of trailing container bytes,
and configured size bounds.

## 9. Binary storage decision

The existing `binary_assets` model is deliberately image-specific: it requires
image dimensions, image MIME validation, Pillow decoding, and an
`assets/images` path. Extending its persisted schema for audio would break
established image contracts.

Speech therefore owns a specialized store, following the established
video-clip precedent, while reusing the provider-neutral
`WorkspaceConfinement` security primitive. WAV files are stored at:

```text
production/{job_id}/assets/speech/speech-{segment_id}.wav
```

Each WAV has a strict `.asset.json` sidecar. Writes are atomic and write-once;
compatible duplicates are idempotent, incompatible duplicates fail closed,
and reads revalidate SHA-256, size, WAV structure, metadata, workspace
confinement, symlinks, reparse points, and hard-link count.

## 10. Recovery

The handler checkpoints `generating` before invoking the provider. Cancellation
propagates as `asyncio.CancelledError` and leaves that checkpoint intact.

On restart:

- `stored` entries are re-read and fully verified;
- a WAV written before its sidecar is verified and its sidecar is safely
  reconstructed;
- a WAV written before the final manifest checkpoint is recovered without a
  provider call;
- a recent `generating` checkpoint returns a transient in-progress result,
  preventing concurrent duplicate generation;
- a stale `generating` checkpoint is retried only because the sole provider is
  local, deterministic, and has no remote operation;
- failed entries may be deliberately retried;
- changed source script identity, checksum, segment identity, or configuration
  fingerprint fails closed.

## 11. Idempotency

Audio identity is stable across attempts and stored outside attempt
directories. Duplicate stage delivery verifies existing assets and emits the
same stable artifact IDs. CAS checkpoints reject stale writers, and a recent
`generating` checkpoint prevents a concurrent duplicate invocation from
calling the provider for the same segment.

The simulator has no billable or remotely uncertain submission.

## 12. Reconciliation

`SpeechGenerationReconciler` is read-only. It scans contractual manifests and
speech asset paths and returns deterministic typed issues for missing or
changed source scripts, missing/corrupt manifests, duplicate or invalid
entries, missing/orphan audio, checksum drift, WAV metadata/duration drift,
terminal incomplete state, unsafe paths, sensitive metadata, and link drift.

It never generates, retries, deletes, repairs, rewrites, or quarantines speech
data.

## 13. Configuration

Safe defaults:

| Setting | Default |
| --- | --- |
| `ORION_SPEECH_GENERATION_PROVIDER` | `simulated` |
| `ORION_SPEECH_GENERATION_VOICE` | `simulated-neutral-v1` |
| `ORION_SPEECH_GENERATION_LANGUAGE` | `es-ES` |
| `ORION_SPEECH_GENERATION_WORDS_PER_MINUTE` | `150` |
| `ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ` | `24000` |
| `ORION_SPEECH_GENERATION_CHANNEL_COUNT` | `1` |
| `ORION_SPEECH_GENERATION_SAMPLE_WIDTH_BYTES` | `2` |
| `ORION_SPEECH_GENERATION_MIN_DURATION_MS` | `250` |
| `ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS` | `120000` |
| `ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES` | `8000000` |
| `ORION_SPEECH_GENERATION_MAX_MANIFEST_BYTES` | `4000000` |
| `ORION_SPEECH_GENERATION_MAX_SCRIPT_BYTES` | `2000000` |
| `ORION_SPEECH_GENERATION_GENERATING_STALE_AFTER_SECONDS` | `30` |

Only `simulated` is selectable. Configuration validation ensures the WAV byte
limit can contain the maximum configured duration and rejects unsafe numeric
limits.

## 14. Security

- no API key, provider URL, authorization setting, or billing flag exists;
- no network or subprocess module is used by the simulated provider;
- no raw bytes are represented or serialized by contracts;
- metadata rejects sensitive keys and absolute paths;
- all paths are relative, contractual, and workspace-confined;
- symlinks, junctions/reparse points, hard links, traversal, incompatible
  duplicates, oversized reads, and corrupt sidecars fail closed;
- exception messages persisted by the handler are stable safe codes only.

## 15. Offline guarantees

Phase 5G.1 made no live provider request, network request, cloud request,
upload, or billable request. It requires no API key, funded account, public
URL, tunnel, cloud account, DaVinci installation, FFmpeg, native audio library,
or external infrastructure.

The simulated speech provider remains the only and default provider.
OpenRouter and billable video requests remain disabled by default, and asset
publishing remains `null`.

## 16. Known limitations

- audio is a deterministic placeholder tone, not human-quality speech;
- there is one segment per script scene; shot-specific narration is not yet
  present in the approved script contract;
- pronunciation notes are not synthesized;
- there is no subtitle alignment, voice cloning, music, effects, mixing,
  loudness normalization, timeline, render, DaVinci, or frontend integration;
- the real-provider uncertain-state policy remains intentionally undefined.

## 17. Future real TTS provider phase

A later phase may add a real adapter only after explicit provider selection,
credential and billing authorization, request idempotency, provider-side job
recovery, response-size limits, license/privacy review, and offline fake
transport tests. It must implement the existing port without moving provider
rules into the handler and must never reinterpret an uncertain request as a
fresh billable submission.
