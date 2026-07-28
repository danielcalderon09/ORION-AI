# Production TTS Provider Evaluation

Status: research complete; provider selection deferred

Research timestamp: 2026-07-28T11:24:00Z

Review no later than: 2026-10-28

Evidence artifact: [`research/tts-provider-evaluation.json`](research/tts-provider-evaluation.json)

Decision record: [`adrs/019-real-tts-provider-selection.md`](adrs/019-real-tts-provider-selection.md)

## Executive outcome

No real TTS provider is selected in Phase 5G.3.

Azure AI Speech is the first controlled-listening candidate because its
official catalog documents Colombian Spanish voices, its REST service can
return production-safe RIFF PCM, and its batch API exposes a client-selected
durable identity, polling, expiry, and word-boundary output. Google Cloud
Text-to-Speech is the second candidate because its public pricing, no-logging
statement, audio formats, and 99.9% TTS SLO are comparatively clear.

That shortlist is not an authorization to integrate either provider. Three
blocking facts remain:

1. Documentation cannot establish naturalness, pronunciation, or consistency
   across Spanish narration segments.
2. Azure's accessible official pricing page did not expose a numeric current
   pay-as-you-go rate for the selected region/currency context.
3. Product and legal review of data processing, disclosure, content policy,
   and commercial terms is incomplete.

Simulated speech remains the only active provider. Billable speech remains
disabled. This phase added no runtime provider, endpoint, credential setting,
SDK, network route, or speech-manifest change.

## Research method and freshness

Research used official-domain web search and direct opening of official
documentation. The OpenAI documentation MCP was registered during the
capability check, but it was not exposed to the running session; the documented
official-web fallback was therefore used for OpenAI. Public webpages, visible
publication/update dates, API documentation, pricing pages, terms, privacy
material, and operational pages could be inspected without authentication.

All evidence was accessed on 2026-07-28. Important visible dates include:

- OpenAI Service Terms: 2026-06-12.
- Microsoft Speech REST documentation: 2026-05-21.
- ElevenLabs Terms of Use: 2026-03-31.
- Google Cloud TTS SSML documentation: 2026-07-22.
- Google Cloud TTS data-logging documentation: 2026-06-09.
- AWS Service Terms: 2026-07-09.
- Amazon ML Language Services SLA: 2023-11-28.

Many living API and pricing pages do not publish an update date. Their source
ledger records `null` rather than inventing one. The machine-readable artifact
contains 40 official sources, access dates, visible update dates, explicit
versus inferred findings, confidence, and ambiguity notes.

### Account-gated or unavailable information

- Numeric Azure pay-as-you-go TTS pricing was not present in the official page
  rendering available to the research tool. It remains unknown.
- Azure resource-specific Service Health is account contextual. A current
  Speech-specific SLA was not verified.
- OpenAI enterprise service-health views and non-standard latency commitments
  are account or commercial-tier contextual.
- ElevenLabs zero retention is enterprise gated, and 44.1 kHz PCM is tied to a
  higher paid tier.
- Real quota allocations and negotiated discounts for all providers are
  account dependent.
- No provider dashboard, audio sample generation, private quote, or sales
  material was accessed.

## ORION decision model

The Phase 5G.3 weights remain the proposed weights because they match ORION's
initial product: Latin American Spanish quality potential is primary, while
submission safety and predictable spend jointly carry more weight than future
styles, cloning, or streaming previews.

| Criterion | Weight | Score 0 | Score 3 | Score 5 |
|---|---:|---|---|---|
| Spanish/Latin American narration suitability | 25% | no reliable Spanish | Spanish, no Colombian specificity | documented Colombian voices and broader LatAm options |
| API and recovery safety | 18% | no usable lifecycle | adaptable synchronous flow | durable identity and recovery-friendly lifecycle |
| Price and cost predictability | 18% | unknown/sales-only | public but incomplete/model-dependent | public, simple, predictable |
| Audio and production compatibility | 12% | incompatible/unknown | conversion or constraints needed | direct production PCM/WAV fit |
| Privacy and data controls | 10% | unclear/material risk | manageable controls | explicit no-training/no-retention posture |
| Timing and alignment | 7% | none documented | manual marks/separate flow | word or character alignment |
| Operational reliability | 5% | materially undocumented | public evidence with gaps | strong public SLA/quota/version evidence |
| Implementation complexity | 5% | very high | medium | low |

Scores are bounded 0–5. The raw weighted score is multiplied by an evidence
confidence factor, then a small unresolved-risk penalty is subtracted. These
numbers are reproducible decision aids, not measurements of audio quality.
Final differences under three points are treated as a tie.

## Provider findings

### OpenAI

Spanish is supported as input, but the official guide says the built-in voices
are optimized for English and does not document a Colombian locale-specific
voice. The speech API is synchronous with chunked streaming and supports MP3,
Opus, AAC, FLAC, WAV, and raw 24 kHz, 16-bit mono PCM. See the official
[TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech) and
[speech endpoint](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create).

The current
[GPT-4o mini TTS model page](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)
documents a 2,000-token input limit, text/audio-token pricing, and a dated
snapshot. The current model catalog surfaced lifecycle/deprecation ambiguity,
while the guide still directs developers to this family. The older
[TTS-1](https://developers.openai.com/api/docs/models/tts-1) and
[TTS-1 HD](https://developers.openai.com/api/docs/models/tts-1-hd) pages retain
clear character pricing.

No durable remote identity, provider-side idempotency key, cancellation
lifecycle, or word-level timing was documented for speech. ORION could wrap a
synchronous result, but an ambiguous post-send timeout must remain `uncertain`
and must never trigger an automatic second billable request.

OpenAI documents that API data is not used for training by default. The speech
endpoint has a 30-day abuse-monitoring retention period and is Zero Data
Retention eligible under the
[endpoint data controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint).
The guide also requires disclosure that the voice is AI generated. Commercial
use remains subject to the current
[Service Terms](https://openai.com/policies/service-terms/); this report makes
no legal conclusion.

Integration complexity: low for direct synchronous audio, but medium
operational risk because model lifecycle and durable submission identity are
not clear enough.

### ElevenLabs

The current model documentation lists broad multilingual support. Multilingual
v2 explicitly includes Spanish from Spain and Mexico; no Colombian
locale-specific voice was documented. Input bounds vary by family: current
documentation lists 5,000 characters for v3, 10,000 for Multilingual v2, and
40,000 for Flash v2.5. See
[TTS capabilities](https://elevenlabs.io/docs/overview/capabilities/text-to-speech).

The API returns streamed MP3, Opus, or raw 16-bit PCM at multiple sample rates.
High-rate PCM is plan gated. The
[timestamped endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/stream-with-timestamps)
returns character-level alignment with generation, which is the strongest
direct alignment fit in the comparison. A synchronous/streaming adapter would
still need to wrap raw PCM as a canonical WAV before the existing speech store
accepts it.

The reviewed API does not expose a durable asynchronous job identity or
provider idempotency primitive. The catalog also needs governance:
[voice documentation](https://elevenlabs.io/docs/overview/capabilities/voices)
currently says default voices expire on 2026-12-31.
[A public status page](https://status.elevenlabs.io/) exists, but no
contractual API SLA was verified.

Public API pricing is character based. Commercial use requires a paid plan.
Default data handling permits service improvement and troubleshooting;
[zero-retention mode](https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode)
is enterprise gated. The
[Terms of Use](https://elevenlabs.io/terms-of-use) contain broad content-license
and data-use provisions plus opt-out controls that require legal review.

Integration complexity: low to medium for generation, medium to high for
stable voice selection, retention posture, and catalog drift.

### Microsoft Azure AI Speech

Azure is the only evaluated provider whose current official
[language catalog](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)
explicitly lists Colombian Spanish prebuilt voices:
`es-CO-SalomeNeural` and `es-CO-GonzaloNeural`. It also lists many other Latin
American locales. This is strong evidence of locale coverage, not evidence of
subjective audio quality.

The
[REST TTS documentation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech)
supports SSML and RIFF PCM at production sample rates including 24 and 48 kHz,
16-bit mono. It documents a ten-minute synchronous output limit, comfortably
above ORION's initial three-minute target.

Azure's
[batch synthesis API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-synthesis)
is the closest match to Phase 5G.2: the client chooses a synthesis identifier,
checkpoints it, polls durable status, downloads output, and can request word or
sentence boundary files. Result locations are temporary signed locations;
future code must consume them transiently and must not persist them.

Microsoft documents that real-time prebuilt TTS text and generated audio are
not retained, while batch data persists until deletion or configured expiry.
See the
[Speech privacy documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/speech-service/text-to-speech/data-privacy-security).
The current
[Microsoft Product Terms](https://www.microsoft.com/licensing/terms/en-US/productoffering/MicrosoftAzure/MOSA)
expressly allow commercial use of paid-tier prebuilt neural TTS output, subject
to input rights and the applicable code of conduct.

The blocking weakness is cost evidence. The official
[pricing page](https://azure.microsoft.com/en-us/pricing/details/speech/) did
not expose a numeric current pay-as-you-go rate in the accessible
region/currency rendering. The number is therefore unknown, not copied from
memory or a third-party article.

Integration complexity: low for synchronous REST, medium for batch. The
client-selected batch identity provides the best documented duplicate-billing
control in this comparison.

### Google Cloud Text-to-Speech

Google's current
[voice catalog](https://cloud.google.com/text-to-speech/docs/voices) documents
Spanish for the United States and Spain across current families, but no
Colombian locale-specific voice. The current model set includes established
Standard, WaveNet, Neural2, Studio and Chirp families plus evolving Gemini TTS
families. Some current Gemini entries are previews, so pinned model identifiers
and a short review interval are mandatory.

[Gemini TTS documentation](https://cloud.google.com/text-to-speech/docs/gemini-tts)
documents LINEAR16/PCM and compressed outputs, single- and multi-speaker modes,
and token bounds. Classic Cloud TTS exposes synchronous audio. The
[long-audio API](https://cloud.google.com/text-to-speech/docs/create-audio-text-long-audio-synthesis)
is asynchronous but requires Cloud Storage and provider identity permissions.
That cloud-storage coupling is unjustified for ORION's initial 30-second to
three-minute use case.

Google supports SSML controls and
[SSML mark timepoints](https://cloud.google.com/text-to-speech/docs/ssml), but
the reviewed generation documentation did not establish automatic word-level
alignment. The
[data-logging documentation](https://cloud.google.com/text-to-speech/docs/data-logging)
says Cloud TTS is stateless and does not log customer text or audio. Google also
publishes a
[99.9% monthly TTS SLO](https://cloud.google.com/text-to-speech/sla).

Prices are public by model family. Gemini output is token priced; the official
page defines 25 audio tokens per second, making the audio component
mathematically normalizable while text input remains additional. Current
[service-specific terms](https://cloud.google.com/terms/service-terms) differ
for generative and preview features and require model-specific legal review.

Integration complexity: medium. Synchronous generation fits an adapter;
long-audio would require a deliberate contract and infrastructure extension.

### Amazon Polly

The official
[voice catalog](https://docs.aws.amazon.com/polly/latest/dg/available-voices.html)
documents Mexican Spanish and United States Spanish voices but no Colombian
locale-specific voice. Standard, Neural, Long-form, and Generative engines have
different voice, rate, and feature availability.

The synchronous
[SynthesizeSpeech API](https://docs.aws.amazon.com/polly/latest/APIReference/API_SynthesizeSpeech.html)
returns MP3, Ogg Vorbis, or raw 16-bit mono PCM. Raw PCM is limited to 8 or
16 kHz, below ORION's current 24 kHz WAV baseline, so the initial format would
need a deliberate validator/storage decision. Synchronous input is bounded to
3,000 billed characters and 6,000 total characters. The
[asynchronous flow](https://docs.aws.amazon.com/polly/latest/dg/asynchronous.html)
has a remote task identity and much larger bounds, but requires S3.

[Speech marks](https://docs.aws.amazon.com/polly/latest/dg/speechmarks.html)
provide sentence, word, viseme, and SSML timing. They are returned instead of
audio and are billed as a separate synthesis request, so timing can nearly
double the relevant character charge and introduces a second idempotency unit.

AWS publishes engine-specific rate/concurrency limits and includes Polly in the
[Amazon ML Language Service SLA](https://aws.amazon.com/ai/services/language-sla/).
The organization-level
[AI services opt-out policy](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_ai-opt-out.html)
is important because AWS otherwise documents content use/storage for service
improvement. Commercial use and output rights remain subject to the
[AWS Service Terms](https://aws.amazon.com/service-terms/) and legal review.

Integration complexity: medium. AWS request signing, PCM wrapping, S3 for
asynchronous output, and a separate timing request are meaningful costs.

## API lifecycle mapping

| Provider | Synchronous | Asynchronous | Streaming | Durable provider identity | Timing path | ORION safety implication |
|---|---|---|---|---|---|---|
| OpenAI | yes | not documented | chunked audio | not documented | none documented | post-send ambiguity must remain `uncertain` |
| ElevenLabs | yes | not documented | audio; timestamped stream | not documented | character alignment with generation | same no-resubmit rule |
| Azure | yes | batch | SDK/REST options vary | client-selected batch ID | word/sentence boundaries | strongest direct Phase 5G.2 fit |
| Google | yes | long-audio operation | model/API dependent | operation identity | SSML marks | long audio requires Cloud Storage |
| Amazon Polly | yes | synthesis task | response stream | task ID | separate speech-marks request | async requires S3; timing is a second billable request |

No provider documentation justified weakening Phase 5G.2. `submitting` without
a durable provider identity still becomes `uncertain`; `uncertain` is never
automatically resubmitted.

## Text, request, and control findings

| Provider | Documented initial-use bound | Pauses/pronunciation | Long-form implication |
|---|---|---|---|
| OpenAI | GPT-4o mini TTS: 2,000 input tokens | instructions and punctuation; no SSML documented | segment within model bound |
| ElevenLabs | 5k/10k/40k chars by current model | pronunciation dictionaries and continuity context | Multilingual v2/Flash bounds cover initial use |
| Azure | synchronous output up to ten minutes | SSML rate, pitch, pauses, pronunciation, style where voice supports it | batch optional, not required initially |
| Google | Gemini: 8,192 input tokens; long audio up to documented large input | SSML marks, pauses, say-as, phonemes | long audio adds Cloud Storage |
| Amazon Polly | sync: 3k billed/6k total chars; async: 100k billed/200k total | SSML and lexicons | async adds S3 |

Limits can vary by model, engine, region, quota, or account. Future capability
discovery must pin the chosen model and revalidate these limits rather than
treating this table as runtime configuration.

## Pricing snapshot and illustrative costs

Assumption: one minute equals 130–170 Spanish words and 850–1,150 characters
including spaces. Character costs are linear. The 60-second short is the same
range as one minute. Taxes, plan discounts, quotas, free allowances, region,
currency conversion, overage rules, and extra timing calls are excluded.

All arithmetic in the evidence artifact uses decimal strings and is validated
with Python `Decimal`; no persisted monetary float is used.

| Provider/tier | Official unit | 1 min / 60s short | 10 min | 100 min |
|---|---:|---:|---:|---:|
| OpenAI TTS-1 | $15 / 1M chars | $0.01275–0.01725 | $0.1275–0.1725 | $1.275–1.725 |
| OpenAI TTS-1 HD | $30 / 1M chars | $0.0255–0.0345 | $0.255–0.345 | $2.55–3.45 |
| ElevenLabs Flash/Turbo API list | $0.05 / 1k chars | $0.0425–0.0575 | $0.425–0.575 | $4.25–5.75 |
| ElevenLabs Multilingual/v3 API list | $0.10 / 1k chars | $0.085–0.115 | $0.85–1.15 | $8.50–11.50 |
| Azure neural pay as you go | numeric rate unavailable | unknown | unknown | unknown |
| Google Neural2/WaveNet | $16 / 1M chars | $0.0136–0.0184 | $0.136–0.184 | $1.36–1.84 |
| Google Chirp 3 HD | $30 / 1M chars | $0.0255–0.0345 | $0.255–0.345 | $2.55–3.45 |
| Google Gemini 2.5 Flash TTS | $10 / 1M audio tokens + text | $0.015 + text | $0.15 + text | $1.50 + text |
| Amazon Polly Standard | $4 / 1M chars | $0.0034–0.0046 | $0.034–0.046 | $0.34–0.46 |
| Amazon Polly Neural | $16 / 1M chars | $0.0136–0.0184 | $0.136–0.184 | $1.36–1.84 |
| Amazon Polly Generative | $30 / 1M chars | $0.0255–0.0345 | $0.255–0.345 | $2.55–3.45 |

OpenAI GPT-4o mini TTS minute costs remain unknown because no undocumented
audio-token-to-duration rule was used. Google Gemini estimates include only the
documented audio-output component. Polly speech marks are charged as a separate
request at the relevant engine's character price. Azure remains unknown.

Sources:
[OpenAI TTS-1](https://developers.openai.com/api/docs/models/tts-1),
[OpenAI TTS-1 HD](https://developers.openai.com/api/docs/models/tts-1-hd),
[ElevenLabs API pricing](https://elevenlabs.io/pricing/api),
[Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/),
[Google Cloud TTS pricing](https://cloud.google.com/text-to-speech/pricing),
and [Amazon Polly pricing](https://aws.amazon.com/polly/pricing/).

## Privacy, security, and commercial posture

| Provider | Official data-use/retention finding | Commercial/content finding | Required review |
|---|---|---|---|
| OpenAI | API no-training default; 30-day speech abuse monitoring; ZDR eligible | AI-voice disclosure; current API terms and indemnity exclusions apply | confirm project ZDR eligibility, disclosure UI, model terms |
| ElevenLabs | default retention/service improvement; enterprise ZRM | paid commercial use; broad service/data license and policy controls | legal approval and explicit retention mode |
| Azure | real-time prebuilt text/audio not retained; batch retained until deletion/expiry | paid prebuilt neural output explicitly commercially usable | regional processing, DPA, code of conduct |
| Google | Cloud TTS documented stateless/no text or audio logging | generative/preview service-specific terms vary | selected-model term classification and region |
| Amazon Polly | service-improvement use unless organization opt-out | service terms apply; no legal conclusion here | mandatory organization opt-out and output-right review |

No voice cloning should be evaluated in the first integration. All custom voice
paths add consent, impersonation, biometric/voice-likeness, retention, and
commercial restrictions that are outside the initial use case.

## ORION Phase 5G.2 compatibility matrix

Legend: `D` directly supported by the provider-neutral architecture, `A`
supported with an adapter, `E` requires a contract/infrastructure extension,
`N` provider does not support the lifecycle, `U` unknown from official docs.

| Phase 5G.2 surface | OpenAI | ElevenLabs | Azure | Google | Polly |
|---|:---:|:---:|:---:|:---:|:---:|
| `SpeechProviderCapabilities` | A | A | A | A | A |
| `SpeechVoiceCapability` | A | A | A | A | A |
| `SpeechAudioFormatCapability` | A | A | A | A | A |
| `SpeechPricingCapability` | A | A | U | A | A |
| `SpeechRemoteGenerationMode` | A | A | A | A | A |
| `SpeechVoiceSelector` | A | A | A | A | A |
| `SpeechCostEstimator` | A | A | U | A | A |
| `SpeechBillableRequestGate` | D | D | D | D | D |
| `RemoteSpeechJobRecord` | A | A | A | A | A |
| synchronous provider port | A | A | A | A | A |
| asynchronous provider ports | N | N | A | E | E |
| polling | N | N | A | A | A |
| downloader | N | N | A | E | E |
| recovery policy | A | A | A | E | E |
| local WAV validation | D | E | D | A | E |
| audio store | A | A | A | A | A |

Key mismatches:

- Azure numeric pricing cannot pass the billable gate until a dated official
  price is captured and authorized.
- ElevenLabs raw PCM needs deterministic WAV wrapping and stable voice-ID
  governance.
- Google and Polly long-audio modes would contaminate an initial adapter with
  cloud-object-storage concerns; do not implement those modes first.
- Polly's raw PCM sample-rate ceiling differs from the current 24 kHz baseline.
- OpenAI, ElevenLabs, and Google's synchronous paths provide no reviewed
  durable provider identity. ORION's ambiguous-submission protection is
  therefore mandatory.

## Weighted result

| Rank | Provider | Raw / 100 | Evidence factor | Risk penalty | Final / 100 | Confidence |
|---:|---|---:|---:|---:|---:|---|
| 1 | Azure | 89.0 | 0.84 | 7 | 67.8 | medium |
| 2 | Google | 77.0 | 0.90 | 4 | 65.3 | high |
| 3 | Amazon Polly | 74.9 | 0.91 | 4 | 64.2 | high |
| 4 | ElevenLabs | 73.2 | 0.88 | 6 | 58.4 | medium-high |
| 5 | OpenAI | 62.7 | 0.82 | 7 | 44.4 | medium |

Azure, Google, and Polly are within the documented near-tie range after
confidence and risk. Azure's raw use-case fit is strongest, but its unverified
numeric price and absent listening evidence are blocking. This is why the
result is not “Azure selected.”

### Blocking, manageable, deferred, and unknown risks

- **Blocking:** no controlled Spanish listening result; Azure numeric price
  unavailable; legal/product approval incomplete.
- **Manageable:** synchronous ambiguity through existing `uncertain` state;
  exact model/voice pinning; provider capability snapshots; retention
  configuration; bounded segment sizes.
- **Deferred:** custom voices, cloning, multi-speaker narration, emotion/style
  controls, live streaming, long-form cloud-output workflows.
- **Unknown:** negotiated prices, real account quotas, region-specific
  availability for the eventual deployment, and subjective voice consistency.

Providers should not be integrated yet:

- OpenAI until the recommended TTS model lifecycle is unambiguous and Spanish
  listening evidence materially improves its fit.
- ElevenLabs until retention, voice-ID stability, and commercial plan choices
  receive explicit approval.
- Amazon Polly until a 24 kHz production-format strategy and separate
  speech-marks billing/recovery unit are justified.
- Google long-audio and Polly asynchronous modes until ORION actually needs
  long-form cloud storage.

## Decision and safest implementation order

Decision: **no provider selected yet**.

If the blockers are cleared, the safest evaluation and implementation order is:

1. Run a separately authorized, tightly budgeted blind listening test for
   Azure and Google first; add Polly as the cost/control benchmark.
2. Verify Azure numeric pricing and all candidate regional availability on the
   same day as the test.
3. Complete legal/product review for the exact model, voice, region, retention
   mode, and disclosure.
4. Select one provider through a new accepted ADR.
5. Implement only that provider's synchronous 24 kHz mono PCM/WAV path first.
6. Exercise one pre-authorized low-cost segment through the Phase 5G.2 durable
   gate, never via an ad hoc script.
7. Add alignment only after the base audio path is recovered and reconciled.
8. Add asynchronous batch only if short-form throughput proves it necessary;
   Azure batch is the first compatible candidate.

No provider fallback should occur within a billable attempt. A different
provider is a new explicit request identity, estimate, authorization, and
durable checkpoint.

## Future controlled listening-test plan

This plan was not executed in Phase 5G.3.

### Fixed Spanish samples

1. **Neutral opening:** “Hoy exploramos una idea sencilla: convertir una
   historia breve en un video claro, cercano y fácil de recordar.”
2. **Numbers and dates:** “El martes 17 de noviembre de 2026, el proyecto
   alcanzó un 37,5 por ciento de avance y costó 1.249 pesos con 50 centavos.”
3. **Abbreviations and technical terms:** “ORION usa IA, una API y audio WAV a
   veinticuatro kilohercios antes de preparar la edición en DaVinci Resolve.”
4. **Colombian pronunciation:** “Camila viajó de Bogotá a Medellín, pasó por
   el Chocó y terminó su recorrido cerca del río Magdalena.”
5. **Punctuation and pauses:** “¿Qué cambió? Primero, la voz; después, el
   ritmo. Y al final… una pausa breve, pero intencional.”
6. **Foreign product names:** “OpenAI, ElevenLabs, Microsoft Azure, Google
   Cloud y Amazon Polly ofrecen enfoques distintos para texto a voz.”
7. **Restrained expressive close:** “No buscamos exagerar: queremos una voz
   confiable, natural y serena que acompañe la historia sin distraer.”

### Method

- Generate exactly one candidate voice per provider using the same seven
  normalized texts, language request, speaking-rate target, and no custom
  voice.
- Preserve raw provider outputs. Create blind comparison copies as mono,
  24 kHz, 16-bit PCM WAV with identical leading/trailing silence and loudness
  target. Do not use destructive enhancement or denoising.
- Randomize opaque sample IDs separately for every listener. Hide provider,
  model, voice, and price until scoring is locked.
- Use at least five fluent Latin American Spanish listeners, preferably seven,
  with at least three familiar with Colombian Spanish.
- Score naturalness (25%), pronunciation (20%), Latin American neutrality
  (20%), cross-segment consistency (15%), prosody/pacing (10%), and artifacts
  (10%). Also record forced-choice preference and free-text defects.
- Include a second pass over concatenated scene segments to detect voice drift,
  discontinuities, and punctuation artifacts.
- Set a hard total budget ceiling of USD 10 and a per-provider ceiling before
  any request. The Phase 5G.2 billable gate, durable fingerprints, and
  pre-submission checkpoints remain mandatory. Free credits do not substitute
  for authorization.
- Stop before generation if retention, commercial terms, price, region, or
  disclosure is unresolved. Stop a provider after two systemic failures, any
  unexpected charge path, or any unsafe submission ambiguity.
- Use only the fixed public test text; do not submit personal, confidential, or
  unreleased narration.

Listening results replace the “Spanish suitability potential” documentation
score with measured evidence. A provider must clear minimum thresholds for
pronunciation, consistency, no critical number/name errors, and cost ceiling.
The ADR can become accepted only after those results and legal approval.

## Phase 5G.3B preparation package

Phase 5G.3B converts this future plan into strict, linked, offline artifacts
without executing it. The package is documented in
[`PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md`](PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md)
and
[`PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md`](PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md).

It preserves Azure, Google, and Amazon Polly as blocked candidate slots; adds
an eighth continuity sample; fixes common audio-normalization, evaluator,
scoring, critical-failure, and stopping rules; and expands the matrix to 24
generation units. Every unit remains `not_authorized`, `blocked`, and `absent`.
The USD 10 amount remains a policy ceiling, not authorization.

No Phase 5G.3 ranking or provider decision changed.

## Security and offline confirmation

Phase 5G.3 used only public, unauthenticated documentation. It created no
provider account; accessed no dashboard; stored no cookie, credential,
authorization header, signed result location, private URL, private quote, or
account identifier; installed no provider SDK; and made no API generation,
cloud, upload, or billable request.

The evidence JSON is research data, not runtime configuration. Its validator
rejects duplicate JSON keys, non-finite numbers, JSON floats, duplicate source
or provider IDs, unreferenced material findings, invalid score arithmetic,
unsafe sensitive fields, non-canonical serialization, and stale review order.

Prices and capabilities are a dated snapshot. Re-run the research and listening
gate before implementation.
