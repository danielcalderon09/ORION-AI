# Production Real TTS Preparation

## 1. Purpose

Phase 5G.2 prepares ORION for a future real text-to-speech provider without
selecting or enabling one. It adds provider-neutral capability, voice, pricing,
authorization, fingerprint, durable remote-job, recovery, and reconciliation
contracts.

The phase does not generate a real voice.

## 2. Explicit offline status

All Phase 5G.2 behavior is local and deterministic. Capability data is static,
prices in tests are obviously fake, remote records use local files, and the
disabled provider raises before external activity.

No API key, provider URL, funded account, public asset URL, cloud account, live
discovery, upload, or provider request is required.

## 3. Provider-neutral capability model

`SpeechCapabilitySnapshot` contains a versioned, immutable audit of a
`SpeechProviderCapabilities` catalog. Each model describes:

- bounded input characters and UTF-8 bytes;
- maximum output duration;
- exact voices and languages;
- exact formats, MIME types, extensions, sample rates, channels, and widths;
- supported synchronous, asynchronous, and streaming modes;
- speaking-rate, pitch, style/emotion, timestamp, word-timing, and
  character-timing support;
- deterministic-output claims;
- provider-side idempotency and cancellation support;
- provider-neutral pricing capability.

Models, voices, languages, formats, and generation modes must be unique.
Unknown fields and unsupported schema versions fail validation. Raw discovery
responses are never persisted.

`StaticSimulatedSpeechCapabilitySource` describes the existing deterministic
tone provider. `DisabledRemoteSpeechCapabilitySource` always raises a typed
configuration error and performs no discovery.

## 4. Voice selection

`SpeechVoiceSelector` requires an exact provider, model, language, format,
sample rate, channel count, generation mode, and bounded input size.

It does not silently substitute a language, model, or voice. Voice fallback is
allowed only when the request explicitly selects
`explicit_model_default`; the chosen default must already exist in the audited
model capability.

Speaking rate, style, timestamps, and alignment requirements must be
explicitly supported. Selection is deterministic and records the capability
snapshot hash and selection reason.

## 5. Generation modes

The capability vocabulary represents:

- `synchronous`: request followed by an audio result;
- `asynchronous`: submit, poll by durable identity, then download;
- `streaming`: provider capability only.

Separate protocols model synchronous generation, asynchronous submission,
remote status polling, and output downloading. No streaming consumer or
playback path exists.

## 6. Pricing and Decimal usage

Pricing supports:

- per character;
- per UTF-8 byte;
- per token;
- per second;
- per minute;
- per request;
- fixed base plus a supported usage unit;
- unknown.

All persisted monetary values use `Decimal`. Float monetary input is rejected.
Currency uses an uppercase three-letter code. No current or real provider price
is included.

## 7. Cost estimation

`SpeechCostEstimator` uses normalized character length, UTF-8 byte length,
optional token estimate, estimated duration, request count, selected model and
voice, and a versioned pricing snapshot.

It returns minimum and maximum cost. Equal rates produce exact confidence;
different rates produce bounded confidence. Unknown pricing, a missing token
estimate for token pricing, or identity drift fails closed.

A zero estimate never implies authorization. Explicit authorization remains
mandatory.

## 8. Billable authorization gate

`SpeechBillableRequestGate` requires all of:

1. a non-simulated provider;
2. billable requests explicitly enabled;
3. a non-disabled provider selection;
4. valid provider configuration;
5. an available live adapter;
6. an exact capability snapshot;
7. known, bounded pricing;
8. explicit cost authorization in the same currency;
9. an estimate within the authorization ceiling;
10. a deterministic request fingerprint;
11. a durable `prepared` record;
12. permission for a first submission;
13. no unresolved uncertain submission.

The presence of credentials could not satisfy this gate, and no credential
field exists in its contracts.

Normal Phase 5G.2 settings can never satisfy the gate because the only remote
provider value is `disabled` and billable requests must remain false.

## 9. Request fingerprint

The SHA-256 fingerprint uses canonical JSON over:

- source script artifact ID and checksum;
- segment ID and normalized-text hash;
- provider, model, voice, and language;
- speaking rate;
- format, sample rate, and channel count;
- capability and pricing snapshot hashes;
- generation mode;
- safe provider-neutral options.

Changing any billable or output-affecting field changes the fingerprint.
Python's unstable `hash()` is not used.

## 10. Durable remote speech-job records

Remote preparation uses a separate `RemoteSpeechJobRecord` schema, version
`1.0.0`, at:

```text
production/{job_id}/generating_narration/attempt-{attempt_number}/
remote-speech-jobs/{segment_id}.json
```

The record stores stable source hashes, request fingerprint, snapshot hashes,
safe remote identities, state timestamps, poll counters, Decimal estimate and
authorization, output expectations, optional reported cost, and verified
output metadata.

It never stores narration text, audio bytes, API keys, headers, credentials,
request bodies, provider response bodies, signed download URLs, provider URLs,
or private paths.

Persistence uses strict canonical UTF-8 JSON, newline termination,
duplicate-key and NaN/Infinity rejection, bounded reads, workspace confinement,
link rejection, atomic replace, fsync where supported, write-once creation,
file locking, and compare-and-swap checkpoints.

The Phase 5G.1 `speech-generation-manifest.json` schema remains unchanged.

## 11. Submission lifecycle

The future safe lifecycle is:

1. normalize a provider-neutral request;
2. resolve audited capability;
3. select an exact model and voice;
4. estimate cost;
5. obtain explicit authorization;
6. compute the fingerprint;
7. create a `prepared` record;
8. pass the billable gate;
9. checkpoint `submitting`;
10. make exactly one submission attempt;
11. persist returned remote identity immediately;
12. poll or download only through that identity.

No implementation invokes this lifecycle in Phase 5G.2.

## 12. Ambiguous-submission no-retry rule

A timeout or cancellation after the `submitting` checkpoint may mean that a
billable provider accepted the request. If no durable remote identity exists,
recovery classifies the record as `uncertain`.

`uncertain` has no transition back to `prepared` or `submitting`. It requires
deliberate manual resolution. Automatic fresh submission is forbidden even
when the provider offers an idempotency key.

## 13. Recovery policy

Recovery classifications are:

- `prepared`: may perform the first authorized submission;
- `submitting` without durable identity: mark uncertain;
- `uncertain`: manual review, never automatic resubmission;
- `submitted`, `pending`, or `processing`: poll by durable identity;
- `completed` without local audio: download;
- verified local audio: recover it before any provider operation;
- failed, cancelled, or expired: stop terminal;
- downloaded output without verified local audio: manual review.

The policy is pure and tested with local records only.

## 14. Reconciliation

`RemoteSpeechJobReconciler` is read-only. It detects missing, corrupt, orphan,
or unsafe records; fingerprint, capability, or pricing drift; missing or
insufficient authorization; invalid or retryable uncertain state; missing
remote identity; missing completed output; local provenance mismatch; and
sensitive fields.

It never discovers capabilities, authorizes, submits, polls, downloads,
generates, repairs, deletes, or mutates.

## 15. Configuration defaults

```text
ORION_SPEECH_GENERATION_PROVIDER=simulated
ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS=false
ORION_SPEECH_GENERATION_REMOTE_PROVIDER=disabled
ORION_SPEECH_GENERATION_REMOTE_MODEL=
ORION_SPEECH_GENERATION_REMOTE_VOICE=
ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST=
ORION_SPEECH_GENERATION_REMOTE_MAX_POLL_ATTEMPTS=120
ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS=5
ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES=1000000
```

Setting billable speech true, selecting another remote provider, supplying a
remote model/voice/cost, or exceeding bounded polling/storage limits fails
startup validation.

## 16. Disabled remote provider

`DisabledRemoteSpeechProvider` is intentionally non-functional. Its
synchronous, submission, polling, and download methods all raise
`RemoteSpeechProviderDisabledError` before I/O.

It imports no HTTP client, SDK, subprocess, provider URL, or credentials. Close
is idempotent. It is not constructed or routed by the normal production
container.

## 17. Security posture

- no secret or URL setting exists for remote speech;
- no real provider SDK or cloud package is imported;
- safe metadata rejects credential-like keys and absolute paths;
- narration text and audio bytes are excluded from durable remote records;
- remote identities accept only a bounded safe character set;
- request and snapshot hashes bind all output-affecting decisions;
- files are bounded, confined, atomic, and protected against symlinks and hard
  links;
- ambiguous requests become non-retryable.

## 18. Why no API key or provider URL exists

No provider has been selected or audited. Adding credentials or endpoints now
would imply a usable integration, broaden the attack surface, and risk
accidental billing. Those settings belong to a later, explicitly authorized
provider phase.

## 19. Why no real provider was selected

Synchronous, asynchronous, and streaming TTS products differ in privacy,
retention, regional processing, pricing, idempotency, cancellation, audio
format, and alignment behavior. Phase 5G.2 captures those decisions without
prematurely binding ORION to one vendor.

## 20. Compatibility with Phase 5G.1

`GENERATING_NARRATION` still uses `SimulatedSpeechGenerationProvider`.
Segmentation, WAV bytes, speech storage paths, speech manifest schema
`1.0.0`, checkpoint behavior, artifacts, and reconciliation remain unchanged.

The only new active resource is the static simulated capability source. Remote
job storage and reconciliation are passive local infrastructure; no remote
record is created during normal narration.

## 21. Known limitations

- no human-quality voice;
- no live discovery;
- no usable remote adapter;
- no streaming consumption;
- no real catalog or pricing;
- no provider cancellation;
- no real alignment/timestamps;
- no automatic resolution of uncertain submissions.

## 22. Future provider-selection phase

A future phase may select one provider only after reviewing privacy,
retention, region, terms, pricing, authentication, transport bounds,
idempotency semantics, request timeout ambiguity, download integrity, and
manual uncertain-job recovery.

That phase must remain opt-in, introduce credentials through secret types, add
an official pinned endpoint, use fake transports for unit tests, and require a
separate explicit live-validation authorization.
