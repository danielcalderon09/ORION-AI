# Controlled OpenRouter scripting (Phase 6C)

## Purpose and scope

Phase 6C adds one optional real provider to the existing `SCRIPTING` stage. It
does not add a stage, change the production-script schema, or alter image,
video, speech, music, subtitle, rendering, validation, or desktop behavior.
`simulated` remains the committed default and every other creative provider
remains simulated.

The adapter is ready for a future controlled test, but this phase made no real
OpenRouter request. It needed no funded account, added no API key, authorized
no billable request, and selected no permanent model.

## Configuration and activation

The closed provider vocabulary is `simulated` and `openrouter`.

```text
ORION_SCRIPTING_PROVIDER=simulated
ORION_SCRIPTING_MODEL=
ORION_SCRIPTING_API_KEY=
ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS=false
ORION_SCRIPTING_ESTIMATED_COST_USD=
ORION_SCRIPTING_MAX_ESTIMATED_COST_USD=
ORION_SCRIPTING_TIMEOUT_SECONDS=120
ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS=1
ORION_SCRIPTING_MAX_RESPONSE_BYTES=2000000
ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES=2000000
ORION_SCRIPTING_TEMPERATURE=0.2
ORION_SCRIPTING_MAX_OUTPUT_TOKENS=4096
```

Explicit OpenRouter activation fails closed unless provider, non-empty secret,
explicit model, billable authorization, positive Decimal estimate, and Decimal
cost ceiling are all valid. The estimate may not exceed the ceiling. An API
key by itself never authorizes spending. There is no fallback to simulated.

No model is committed. Phase 6C.1 proposes Gemini 2.5 Flash Lite as the
economical controlled-test candidate and GPT-4.1 Mini as the quality fallback,
but ADR-020 remains `PROPOSED` because Spanish quality is untested. See
`PRODUCTION_OPENROUTER_SCRIPTING_MODEL_SELECTION.md`.

## Secrets and desktop behavior

The API key is loaded at runtime through the existing `SecretStr` setting. It
is never stored in jobs, artifacts, request records, fingerprints, logs,
exceptions, desktop preferences, the database, or QSettings. The desktop has
no credential field and starts normally with the simulated default. It uses
the same typed backend configuration when OpenRouter is deliberately selected.

## Provider request and structured output

`OpenRouterScriptingProvider` implements the existing `ScriptingProvider` port.
It sends only the source prompt, verified planning identity and checksum,
language, duration, aspect ratio, scene guidance, and the existing production
script JSON schema. Filesystem paths, host identity, render metadata, secrets,
and unrelated artifacts are excluded.

The adapter follows the official chat-completions request shape and Bearer
authentication documented in the [OpenRouter quickstart](https://openrouter.ai/docs/quickstart).
It requests strict `json_schema` structured output as documented by
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
requires providers to support the requested parameters, denies data-collection
providers, requests zero-data-retention routing, disables provider-side
storage, and never accepts an arbitrary URL.

The versioned instruction requests JSON only, Spanish or the requested
language, voice-over-ready narration, clear scenes, duration-aware pacing, no
Markdown commentary, no hidden reasoning, no secrets, and no imitation of
copyrighted characters. Artifacts retain only the instruction template version
and SHA-256, not hidden instructions.

The bounded parser enforces UTF-8, rejects duplicate keys, NaN, Infinity,
Markdown wrappers, oversized bodies, provider wrappers, and extra schema
fields. Pydantic validates the existing `ProductionScript` schema `1.0.0`.
Scene planning therefore consumes exactly the same durable artifact as before.
Provider output is data only; it is never evaluated or executed.

## Duration policy

The local policy supports the desktop profiles of 15, 30, and 60 seconds, as
well as the existing short test profile. It estimates narration at the
configured words-per-minute rate, requires non-empty narration per scene, and
accepts a deliberately broad deterministic word-count band: at least two words
per scene and 20% of nominal speech, up to 160% of nominal speech. Exact spoken
duration is not claimed. Out-of-policy output fails without a paid retry.

## Deterministic identity

The request fingerprint covers provider, model, normalized prompt hash,
planning artifact ID and checksum, language, target duration, aspect ratio,
scene guidance, planning configuration checksum, script schema, instruction
version and hash, temperature, token limit, and structured-output mode.

It excludes the API key, authorization header, timestamps, attempt number,
absolute paths, username, hostname, process ID, and machine identity. Equivalent
retry/resume input therefore retains one identity; any output-affecting policy
change creates a different identity.

## Durable request checkpoint and billable gate

Before transport, a canonical request record is written at:

```text
production/{job_id}/scripting/attempt-{n}/openrouter-scripting-request.json
```

Schema `1.0.0` has the closed states `prepared`, `submitting`, `completed`,
`failed`, and `uncertain`. Storage uses confined paths, bounded strict JSON,
atomic replacement, an exclusive lock, and compare-and-swap transitions. It
stores only a bounded validated script on completion; raw provider responses
and authorization data are never persisted.

The billable gate checks the durable `prepared` checkpoint and cost authority
before transitioning to `submitting`. Only then may the controlled transport
be invoked. The default maximum transport attempts is exactly one.

OpenRouter documents that requests without returned content can still incur a
charge in some failure cases; see [Errors and debugging](https://openrouter.ai/docs/api-reference/errors-and-debugging).
Accordingly, a timeout, connection loss, cancellation, or ambiguous service
failure after the durable submitting checkpoint becomes `uncertain`. ORION
never automatically resubmits an uncertain request. A deterministic provider
rejection becomes `failed` and also has no automatic paid retry.

## Usage and cost metadata

When present, normalized provider ID, finish reason, prompt tokens, completion
tokens, total tokens, and provider-reported cost are retained. The optional
fields follow OpenRouter's [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
Money is represented as a Decimal string, never a float, and absent cost is not
invented. Full response bodies and headers are not durable.

## Recovery and reconciliation

- No record: create `prepared` deterministically.
- Prepared: continue only after explicit authorization.
- Submitting without a durable result: classify `uncertain`; never resend.
- Completed with matching validated script: reuse without transport.
- Failed or uncertain: require deliberate local intervention.
- Configuration or planning identity changed: reject stale/conflicting state.
- Malformed provider response: emit no script and do not retry.

The read-only reconciler reports corrupt or orphan records, stale planning,
fingerprint/model mismatch, uncertain submission, missing or changed completed
script, unsafe retry markers, raw-response markers, and usage/cost
inconsistency. It does not submit, repair, mutate, or delete anything.

## Transport and security boundary

The adapter reuses ORION's controlled OpenAI-compatible asynchronous HTTPS
transport. The endpoint is fixed by composition, response size and timeout are
bounded, redirects to arbitrary hosts are unavailable, TLS verification stays
enabled, and tests inject a deterministic fake transport. Provider routing uses
the official privacy controls described in
[OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

No test contacts OpenRouter. No startup probe or background request exists.
There are no automatic retries, provider SDK, arbitrary provider URL, shell
command, subprocess, UI key storage, or raw provider response artifact.

## First controlled live-test prerequisites

A later, separately authorized phase must:

1. Fund an owner-controlled OpenRouter account and create a local secret.
2. Evaluate and explicitly choose a model for Spanish structured scripting.
3. Set a conservative Decimal cost estimate and authorization ceiling.
4. Reconfirm current official privacy, retention, model, and pricing terms.
5. Set `ORION_SCRIPTING_PROVIDER=openrouter` and explicitly enable billable
   requests only for the test.
6. Observe the durable checkpoint and usage record, then revoke authorization.

Current limitations are intentional: no permanent model, no live benchmark,
no automatic paid retry, no provider-status desktop controls, and no real media
provider beyond scripting. The recommended next phase is one explicitly
budgeted, single-request Spanish model evaluation before any broader provider
activation.
