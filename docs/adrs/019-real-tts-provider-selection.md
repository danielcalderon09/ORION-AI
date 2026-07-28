# ADR-019: Real TTS Provider Selection

## Status

Deferred

## Date

2026-07-28

Review no later than 2026-10-28, and immediately if a shortlisted provider
changes its model, voice, pricing, retention, or commercial terms.

## Context

Phase 5G.1 added durable simulated narration. Phase 5G.2 added provider-neutral
capabilities, exact voice selection, Decimal cost estimation, explicit billable
authorization, durable remote speech-job records, and a no-retry rule for
ambiguous submissions. No real adapter exists.

ORION initially needs neutral, natural Spanish narration for 30-second to
three-minute scene-based videos. Latin American Spanish, later DaVinci
compatibility, deterministic recovery, cost ceilings, and duplicate-billing
protection matter more than voice cloning, long-form cloud jobs, or streaming
previews.

Phase 5G.3 reviewed current official documentation for OpenAI, ElevenLabs,
Microsoft Azure AI Speech, Google Cloud Text-to-Speech, and Amazon Polly. The
full dated evidence is in
[`../PRODUCTION_TTS_PROVIDER_EVALUATION.md`](../PRODUCTION_TTS_PROVIDER_EVALUATION.md)
and
[`../research/tts-provider-evaluation.json`](../research/tts-provider-evaluation.json).

## Decision

Do not select or integrate a real TTS provider yet.

Azure is the primary controlled-listening candidate. Google is the secondary
candidate. Amazon Polly is the operational and cost benchmark. These labels are
evaluation order, not runtime selection.

The decision is deferred because:

1. Official documentation cannot establish subjective Spanish narration
   quality or cross-segment consistency.
2. Azure's numeric current pay-as-you-go price was not verifiable from the
   accessible official page.
3. Exact-model commercial, privacy, retention, regional-processing, and
   disclosure review is incomplete.

## Considered providers and evidence

- **Azure:** strongest paper fit. Official documentation lists Colombian voices
  `es-CO-SalomeNeural` and `es-CO-GonzaloNeural`, RIFF PCM at production sample
  rates, word/sentence boundaries, and a client-selected batch identifier.
  Numeric pay-as-you-go pricing remains unknown.
- **Google:** strong public pricing, stateless/no-logging documentation, PCM
  options, and a public 99.9% TTS SLO. No Colombian voice was documented.
  Long-audio synthesis requires Cloud Storage.
- **Amazon Polly:** predictable character pricing, documented Mexican Spanish,
  speech marks, quotas, and an SLA. Raw PCM is limited to 8/16 kHz, speech marks
  require a separate billed request, and asynchronous output requires S3.
- **ElevenLabs:** broad Spanish coverage, streaming, and character alignment.
  Colombian specificity is not documented; retention, commercial-plan,
  high-rate PCM, and voice-ID expiry require governance.
- **OpenAI:** direct 24 kHz PCM/WAV and a simple synchronous API. Voices are
  documented as optimized for English, timing is absent, durable submission
  identity is not documented, and the current TTS model lifecycle is ambiguous.

All claims are traceable to official sources in the evidence artifact. No
third-party ranking was used.

## Assumptions

- One minute of initial Spanish narration is approximately 130–170 words and
  850–1,150 characters including spaces.
- Short-form generation can use a synchronous path first.
- The Phase 5G.2 billable gate, fingerprint, durable authorization, and
  `uncertain` no-resubmit rule are non-negotiable.
- Provider-generated voice quality requires a controlled listening test.
- No custom voice or cloning is needed initially.

## Alternatives

### Accept Azure now

Rejected. Locale and lifecycle evidence are strong, but price and listening
evidence are incomplete.

### Accept Google now

Rejected. Operational and price evidence are strong, but Colombian voice fit
is not documented and subjective quality is untested.

### Select the lowest published price

Rejected. The lowest nominal character price does not include voice
suitability, timing calls, plan gates, conversion work, privacy controls, or
submission risk.

### Implement several adapters and decide later

Rejected. It would create avoidable provider-specific maintenance and paid test
paths before the product requirement is proven.

### Keep simulated speech indefinitely

Retained as the active safe fallback, but it does not satisfy future
human-quality narration.

## Consequences

- No runtime code, provider dependency, API key, endpoint setting, or billable
  route is added.
- `simulated` remains the active and default narration provider.
- The current speech manifest and stage order remain unchanged.
- Future implementation begins only after a separately authorized listening
  test, current pricing verification, and legal/product approval.
- The first adapter should be synchronous and produce or normalize to mono,
  24 kHz, 16-bit PCM WAV.
- Asynchronous cloud-output modes are deferred for the initial short-form use
  case.
- No automatic cross-provider fallback is permitted. A fallback is a new
  fingerprint, estimate, authorization, and durable attempt.

## Unresolved risks

- Subjective voice quality and Colombian pronunciation.
- Azure list price and region-specific availability.
- Model and voice identifier stability.
- Exact data-processing region, retention settings, and subprocessor terms.
- Output rights, disclosure, prohibited content, and suspension risk.
- Account-specific quotas and negotiated billing.
- Synchronous timeout ambiguity where the provider has no durable identity.

## Phase 5G.3B preparation follow-up

The controlled-test preparation package is documented in
[`../PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md`](../PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md)
and
[`../PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md`](../PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md).

Preparation does not authorize execution. The candidate slots, generation
units, authorization template, scorecards, and results template are committed
in blocked or empty states. A future test needs separate current-price,
privacy, commercial, regional, budget, and execution approval.

Listening results do not automatically select a provider. Accepting an exact
provider/model/voice strategy still requires another ADR. This ADR remains
Deferred.

## Conditions that invalidate this decision

Review immediately if:

- a provider adds or removes Colombian Spanish voices;
- a shortlisted model or voice is deprecated;
- pricing or billing units change;
- retention, training, or commercial terms change;
- a provider adds durable idempotency or removes it;
- ORION's initial format, length, alignment, or region requirements change;
- controlled listening results produce a clear, policy-compliant winner.

## Implementation recommendation

1. Verify current official prices and candidate availability on the test date.
2. Approve exact retention, commercial, content, and disclosure conditions.
3. Run the bounded blind listening test documented in the evaluation.
4. Create a new ADR accepting one exact provider/model/voice strategy.
5. Implement one synchronous adapter behind Phase 5G.2 ports.
6. Validate one small, explicitly authorized request through durable
   pre-submission checkpoints.
7. Add timing and asynchronous operation only after recovery and reconciliation
   are proven.

No action in this ADR authorizes credentials, provider activation, live
generation, or spend.
