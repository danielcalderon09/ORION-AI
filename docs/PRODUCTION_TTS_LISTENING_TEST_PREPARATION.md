# Production TTS Listening-Test Preparation

Status: offline preparation complete; execution not authorized

Created: 2026-07-28

Research review deadline: 2026-10-28

## 1. Purpose and scope

Phase 5G.3B turns the deferred provider evaluation into a reproducible blind
listening-test package without contacting a provider or creating a usable
generation path.

This phase generated no audio, executed no test, selected no provider, used no
provider account, and made no authenticated or billable request. It added no
API key, credential, provider SDK, provider endpoint, cloud storage, HTTP
client, audio processor, FFmpeg command, subprocess, or production-stage route.

The USD 10 amount is a future policy ceiling. It is not authorization. Current
authorization is zero and every generation unit is blocked.

## 2. Package inventory and cryptographic linkage

| Artifact | Purpose |
|---|---|
| [`research/tts-provider-evaluation.json`](research/tts-provider-evaluation.json) | Phase 5G.3 official evidence snapshot |
| [`research/tts-listening-test-candidates.json`](research/tts-listening-test-candidates.json) | blocked provider slots and comparison-audio contract |
| [`research/tts-listening-test-script.es.json`](research/tts-listening-test-script.es.json) | versioned public-safe Spanish samples |
| [`research/tts-listening-test-generation-plan.json`](research/tts-listening-test-generation-plan.json) | deterministic candidate × sample preparation records |
| [`research/tts-listening-test-authorization.template.json`](research/tts-listening-test-authorization.template.json) | deliberately empty future authorization template |
| [`research/tts-listening-scorecard.template.json`](research/tts-listening-scorecard.template.json) | empty pseudonymous evaluator scorecard |
| [`research/tts-listening-results.template.json`](research/tts-listening-results.template.json) | empty descriptive-results template |

Snapshot hashes:

- research: `7fcfb48e479a50e01b78b90175bddadb714a9b6ee161f28211b97898e61de005`;
- candidates: `ccd1a1c7691770a1d2e064f6d39c957970730cf7f66ad5b4142807a0a00c6743`;
- script artifact: `6d689220c423425ede6f9309a973d422225ba5f3cdbc5264032fc5297644f515`;
- script version: `ee9d6e5efe0982de2d2fd95dce33a673af7d3ffa5b319ee01d6ad3492ee57b7d`;
- generation plan: `142eeb2fc5a61d065b523b9523ae0261bd74bef93be7b149e2f31ad1cfb61aa7`;
- composite test plan: `ba6f539046dfddb07475f9fe65b5bf74c53c22ed2e89366ead1d1523c8dbde5c`.

The candidate artifact stores the research hash. The generation plan stores
the research, candidate, and script hashes. Authorization, scorecard, and
result templates store the appropriate derived hashes. Any byte change breaks
validation and requires deliberate regeneration and review.

## 3. Candidate slots

| Candidate | Verified evidence retained | Values deliberately unknown | Status |
|---|---|---|---|
| Azure AI Speech | `es-CO` coverage, native 24 kHz RIFF PCM potential, batch timing evidence | exact model, voice, region, current numeric price, cost, approvals | blocked |
| Google Cloud Text-to-Speech | `es-US`, LINEAR16, public pricing families, SSML marks | exact model, voice, native sample rate, region, current chosen price, cost, approvals | blocked |
| Amazon Polly | `es-MX`, synchronous raw PCM, speech-marks evidence | exact engine, voice, region, current chosen price, cost, approvals | blocked |

Every candidate has:

- `provider_model: null`;
- `provider_voice: null`;
- `region: null`;
- `maximum_estimated_cost: null`;
- `maximum_authorized_cost: null`;
- pending commercial and retention review;
- unverified regional availability;
- `eligibility_status: blocked`.

The artifacts do not turn model-family research into a selected production
configuration.

OpenAI and ElevenLabs are excluded from the initial paid test. OpenAI remains
blocked by Colombian specificity, timing, durable submission identity, and
model-lifecycle ambiguity. ElevenLabs remains blocked by retention mode,
commercial-plan choice, high-rate PCM access, and stable voice governance.
Neither is rejected permanently.

## 4. Fixed Spanish script

The versioned script contains eight samples:

1. neutral narration;
2. dates, percentages, quantities, and currency;
3. abbreviations and technical terms;
4. Colombian place names;
5. punctuation, questions, pauses, and ellipsis;
6. foreign company/product names;
7. calm expressive closing;
8. scene-to-scene continuity.

Each sample records:

- stable ID and schema;
- public-safe text;
- locale;
- duration range;
- critical tokens;
- pronunciation notes;
- error categories;
- external-submission permission;
- Unicode character count;
- UTF-8 byte count;
- SHA-256 of normalized UTF-8.

The text is original test material, contains no private product information or
credentials, and is classified `public_test_text`. It was not submitted
anywhere.

## 5. Text-normalization policy

Normalization is deterministic and provider neutral:

- Unicode NFC;
- CRLF and CR normalize to LF, then multiline samples are rejected;
- outer ASCII spaces are trimmed;
- repeated ASCII spaces collapse to one;
- tabs and other whitespace are rejected;
- punctuation is preserved;
- quotes are not changed;
- the Unicode ellipsis `…` is preserved;
- Spanish decimal commas and thousands separators are preserved;
- abbreviations are preserved exactly;
- no translation;
- no provider-specific rewrite;
- no silent substitution.

Counts use Unicode code points and encoded UTF-8 bytes. A sample hash is
SHA-256 over the normalized UTF-8 bytes. The aggregate script-version hash is
SHA-256 over the ordered compact-canonical sample identities.

## 6. Generation plan

The plan expands three candidates × eight samples into 24 records. Each
generation-unit ID is derived from:

- candidate ID;
- sample ID;
- research snapshot hash;
- candidate snapshot hash;
- script snapshot hash.

The unit contains text hash and future provider-neutral fingerprint inputs, but
does not copy narration text or contain a provider request body.

Every committed unit has:

- `authorization_status: not_authorized`;
- `execution_status: blocked`;
- `output_status: absent`;
- null minimum and maximum estimates;
- null maximum authorized cost;
- null model and voice fingerprint inputs.

## 7. Comparison-audio normalization contract

No audio was read, written, converted, or validated in this phase.

Future comparison copies must be:

- WAV PCM;
- mono;
- 24,000 Hz;
- 16-bit;
- integrated loudness measured using ITU-R BS.1770-4;
- target loudness `-16.0 LUFS`;
- true-peak ceiling `-1.0 dBTP`;
- exactly 250 ms leading silence;
- exactly 250 ms trailing silence.

Only one deterministic resample and deterministic mono downmix are allowed.
External silence may be trimmed before exact padding; internal pauses must not
be changed. Original provider output must be retained separately.

Prohibited transformations include denoising, voice enhancement, artificial
prosody correction, provider-specific EQ, dynamic-range enhancement, and
destructive clipping.

## 8. Budget policy and authorization

The policy currency is USD and the absolute future ceiling is `10.00`.

- Current authorized amount: none.
- Candidate authorization: none.
- Unit authorization: none.
- Free credit: not authorization.
- Subscription allowance: not authorization.
- Account balance: not authorization.
- Unknown price: execution blocker.
- Currency conversion: not approved.

A future uncommitted authorization must contain verified unit maximums,
candidate maximums, and total worst-case cost. Each unit must fit its unit
ceiling, candidate totals must fit candidate ceilings, the overall total must
fit the authorization, and authorization must not exceed USD 10.

The committed authorization template is `draft`; candidate IDs are empty;
authorization and expiry timestamps are null; every approval is false; all
budget fields are null. A future completed authorization must not be committed
to Git.

## 9. Blind identity and package separation

The future blinding algorithm uses HMAC-SHA-256 with an externally supplied
secret of at least 32 bytes and domain-separated inputs:

- test run ID;
- evaluator ID;
- candidate ID;
- sample ID.

Opaque sample IDs have the form `bs-<24 hex characters>`. Evaluator order is a
separate HMAC-derived sort, so different evaluator IDs receive different
orders. IDs do not encode provider order, model, voice, price, or sample
category.

The real key, decoding map, and provider-to-blind mapping must never be
committed or included in evaluator packages. The offline tests use an obviously
fake `TEST-ONLY` fixture key. Tooling neither stores nor prints a supplied key.

Audio filenames may contain only run/package/blind IDs. Provider metadata and
audio tags are forbidden in evaluator copies.

## 10. Evaluator package

Each future package contains:

- opaque evaluator-package ID;
- opaque scorecard ID;
- randomized blind sample list;
- evaluator instructions and rubric;
- fixed playback instructions;
- empty scorecard template;
- consent and data-minimization statement.

Evaluator instructions:

- use headphones when practical and a quiet environment;
- use the same playback device and volume for the package;
- replay clips as needed without editing or signal analysis;
- do not attempt to identify providers;
- do not inspect file metadata;
- do not discuss scores before submission;
- report defects against the opaque sample ID;
- complete every required score and forced choice independently.

Pseudonymous metadata is limited to:

- evaluator ID;
- `fluent_latin_american` Spanish fluency category;
- Colombian-Spanish familiarity: `none`, `general`, or `strong`;
- device category: `headphones`, `earbuds`, `speakers`, or `other`;
- completion timestamp;
- bounded optional safe notes.

No legal name, address, phone number, identity document, or sensitive
demographic attribute is requested.

## 11. Scoring rubric

All six categories are required on a 1–5 scale:

| Category | Weight | Score 1 | Score 3 | Score 5 |
|---|---:|---|---|---|
| Naturalness | 25% | clearly synthetic or distracting | usable but noticeable synthesis | consistently natural for narration |
| Pronunciation | 20% | frequent or material errors | minor errors without lost meaning | accurate critical tokens and fluent delivery |
| Latin American neutrality | 20% | unsuitable or strongly conflicting delivery | generally acceptable with some regional mismatch | neutral, credible LatAm narration |
| Cross-segment consistency | 15% | material voice/rate drift | small detectable variation | stable identity, tone, and pacing |
| Prosody and pacing | 10% | unnatural emphasis or timing | acceptable with isolated awkwardness | controlled pauses and narrative flow |
| Artifacts | 10% | clipping, corruption, or repeated artifacts | minor isolated artifacts | clean and intelligible |

Score 2 lies between 1 and 3; score 4 lies between 3 and 5. `N/A` is not
allowed for a required category. An unplayable or unscorable clip is an
incident, not a neutral score. Missing responses make the scorecard invalid.

Additional observations include forced choice per script sample, critical
number/currency or date error, Colombian place-name error, abbreviation error,
unnatural pause, voice drift, clipping/encoding artifact, intelligibility
failure, evaluator confidence, and a bounded safe comment.

## 12. Critical-failure policy

An average cannot override a critical failure.

Immediate disqualification:

- missing audio;
- corrupt output;
- unsafe content substitution;
- provider returned materially different text;
- unexpected charge;
- ambiguous billable submission;
- terms/privacy mismatch;
- region or voice unavailable.

Disqualification after two independent reports on the same output:

- critical number/currency error;
- critical date error;
- unintelligible segment;
- repeated truncation.

Disqualification after three independent reports on the same output:

- critical Colombian place-name failure;
- serious voice drift.

These thresholds are fixed before provider identities or results are revealed.
Execution also stops after two systemic generation failures for one provider,
regardless of listener scoring.

## 13. Minimum evaluators

A completed test requires:

- at least five fluent Latin American Spanish evaluators;
- seven preferred;
- at least three self-reporting `strong` Colombian-Spanish familiarity;
- no duplicate evaluator or scorecard;
- every candidate × sample score;
- one forced choice for each script sample;
- identical normalization specification for all candidates;
- uncompromised blinding.

Failure of any minimum blocks result acceptance.

## 14. Aggregation

The pure offline calculator:

- verifies blind IDs against an external mapping;
- rejects unknown IDs and duplicate submissions;
- enforces complete scorecards;
- computes category medians as primary statistics;
- computes category means only as secondary statistics;
- calculates per-sample medians;
- calculates weighted median/mean scores to one decimal place;
- reports weighted-score interquartile range;
- counts forced-choice wins;
- counts critical failures and applies the fixed thresholds;
- never exposes provider identity in evaluator-facing output.

The six weights sum to 100%. Decimal arithmetic is used. Results are
descriptive. Product judgment remains separate, and no calculator output
automatically selects a provider. An accepted provider still requires a new
ADR.

## 15. Stopping rules

Stop before any request when:

- research is stale;
- any snapshot hash differs;
- exact model, voice, price, region, or availability is unverified;
- retention, commercial, or privacy review is incomplete;
- authorization is missing, draft, or expired;
- any worst-case cost exceeds unit, candidate, total, or USD 10 limits;
- credential scope is broader than minimally required;
- output retention is not approved.

Stop a future run when:

- submission becomes ambiguous;
- an unexpected charge path appears;
- two systemic provider failures occur;
- output differs from the fixed text or audio contract;
- the provider changes the selected model or voice;
- unsafe substitution occurs;
- the authorized stopping threshold is reached.

Never retry an ambiguous billable submission. Never automatically switch
providers after a stop.

## 16. Validator and security posture

Run:

```text
python scripts/validate_tts_provider_evaluation.py
python scripts/validate_tts_listening_test_plan.py
```

The validator performs local reads only. It enforces strict UTF-8, no BOM,
canonical sorted JSON, duplicate-key rejection, non-finite/float rejection,
schemas, hashes, source references, freshness, exact candidates, null unknowns,
the complete 24-unit matrix, blocked statuses, the USD 10 policy, empty
authorization/results, and absence of secrets, endpoints, cloud identifiers,
request bodies, HMAC seeds, and decoding maps.

It contains no network, cloud, audio, FFmpeg, or subprocess import.

## 17. Known limitations

- No exact model, engine, or voice is selected.
- Azure numeric pricing remains unknown.
- Google and Polly prices require same-day verification for the exact choice.
- Regions and account quotas are unverified.
- No audio exists, so normalization tooling is not implemented or tested on
  media.
- No real evaluator package or decoding map exists.
- The descriptive method is intentionally modest for a small listener sample.
- Preparation does not resolve legal or commercial questions.

The active production pipeline remains unchanged: simulated speech is active,
billable speech is false, and the remote provider is disabled.
