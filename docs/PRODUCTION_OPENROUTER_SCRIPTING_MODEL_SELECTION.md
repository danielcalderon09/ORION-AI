# OpenRouter scripting model evaluation (Phase 6C.1)

## Objective and research date

This phase evaluates public OpenRouter models for ORION's existing Spanish
short-video SCRIPTING contract. Research was performed on 2026-08-01 UTC. It is
a dated public snapshot, not a benchmark or runtime activation.

No secret was read, no authenticated endpoint was accessed, no account balance
was required, and no free or paid generation request occurred.

## Official sources and discovery

Only official OpenRouter sources were used:

- public unauthenticated [`GET /api/v1/models`](https://openrouter.ai/api/v1/models);
- [Models API reference](https://openrouter.ai/docs/api/api-reference/models/get-models);
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs);
- [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection);
- [pricing](https://openrouter.ai/pricing);
- [`:free` variants](https://openrouter.ai/docs/guides/routing/model-variants/free)
  and the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router);
- [ZDR](https://openrouter.ai/docs/guides/features/zdr);
- public model and endpoint pages recorded in the machine-readable artifact.

The catalog contained 336 models. The broad deterministic filter retained 283
with text input/output, current public identifiers, at least 16K context,
public token pricing or explicit zero pricing, no expiration, and
`response_format` or `structured_outputs` metadata. This is discovery, not a
claim that all 283 are equally reliable.

## Shortlist

Paid candidates:

| Model | Input / 1M | Output / 1M | Public endpoint records | Role |
|---|---:|---:|---:|---|
| Gemini 2.5 Flash Lite | $0.10 | $0.40 | 5 | Proposed primary |
| GPT-4.1 Mini | $0.40 | $1.60 | 3 | Proposed quality fallback |
| Qwen3 30B A3B Instruct 2507 | $0.04815 | $0.19305 | 5 | Economical alternative |
| Mistral Small 3.2 24B | $0.075 | $0.20 | 3 | Economical alternative |

Free candidates:

- `google/gemma-4-26b-a4b-it:free` — proposed experiment-only model;
- `openai/gpt-oss-20b:free` — deferred free alternative because reasoning is
  mandatory and free-tier evidence is weaker.

`openrouter/free` is not a permanent candidate. Its official documentation says
it selects an available free model at random. That is incompatible with ORION's
model-bearing fingerprint, deterministic recovery, and output consistency.

## Structured-output findings

All shortlisted catalog entries expose `response_format` or
`structured_outputs`. Public endpoint metadata showed the required parameter
across five Gemini, three GPT-4.1 Mini, five Qwen, and three Mistral records.
Gemini and GPT-4.1 Mini pages also publish current provider-specific structured
output error observations. These observations improve confidence but do not
replace a test using ORION's exact schema and ZDR route filter.

Unknown structured support receives no credit. Mistral Small 3.1, for example,
was excluded because its current catalog entry did not expose either required
parameter while 3.2 did.

## Spanish suitability

No model generated Spanish in this phase. Spanish scores therefore represent
documented potential only:

- Qwen has the strongest explicit evidence: its official page describes
  multilingual understanding, instruction following, and WritingBench results.
- Gemini and GPT-4.1 Mini have strong efficiency/instruction evidence but no
  isolated Latin American Spanish result in the consulted OpenRouter sources.
- Mistral's consulted page documents instruction and structured-output
  improvements, but did not explicitly establish Spanish quality.
- Neither free candidate has sufficient Spanish evidence for production.

Hooks, idiom, narration rhythm, factual restraint, visual direction, and scene
coherence remain unproven until a controlled real test.

## Token and cost assumptions

Input range: 2,500–5,000 tokens for system instruction, JSON Schema, planning
artifact, and request fields. Output ranges:

- 15 seconds: 150–400 tokens;
- 30 seconds: 250–700 tokens;
- 60 seconds: 500–1,300 tokens.

A missing catalog request fee is treated as zero only for estimation, while the
quoted request-fee field remains unknown/null. Costs use Decimal arithmetic and
exclude credit-purchase fees, caching discounts, retries, reasoning tokens, and
future price changes.

| Model | 15 s | 30 s | 60 s | 100 × 30 s | 1,000 × 30 s |
|---|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash Lite | $0.000310–0.000660 | $0.000350–0.000780 | $0.000450–0.001020 | $0.035–0.078 | $0.350–0.780 |
| GPT-4.1 Mini | $0.001240–0.002640 | $0.001400–0.003120 | $0.001800–0.004080 | $0.140–0.312 | $1.400–3.120 |
| Qwen3 30B Instruct | $0.000149–0.000318 | $0.000169–0.000376 | $0.000217–0.000492 | $0.0169–0.0376 | $0.1686–0.3759 |
| Mistral Small 3.2 | $0.000218–0.000455 | $0.000238–0.000515 | $0.000288–0.000635 | $0.0238–0.0515 | $0.2375–0.5150 |
| Named free variants | $0 | $0 | $0 | $0 | $0 |

## Weighted scoring

Weights are structured output 30%, Spanish potential 25%, cost 20%, latency and
efficiency 10%, availability/routing 10%, and context fit 5%. Category scores
are bounded 0–10. Each contribution is confidence-adjusted and receives an
explicit risk penalty. The validator reproduces every calculation.

| Rank | Model | Raw weighted | Confidence/risk-adjusted |
|---:|---|---:|---:|
| 1 | Gemini 2.5 Flash Lite | 8.7 | 7.6 |
| 2 | GPT-4.1 Mini | 8.4 | 7.3 |
| 3 | Qwen3 30B A3B Instruct | 8.7 | 7.2 |
| 4 | Mistral Small 3.2 24B | 8.1 | 6.3 |
| 5 | Gemma 4 26B A4B free | 7.6 | 5.3 |
| 6 | gpt-oss-20b free | 7.4 | 5.2 |

The result intentionally avoids false precision in prose. Gemini wins only
after confidence adjustment; Qwen's raw score is slightly higher. A real
Spanish/schema test could reverse the first three.

## Proposed roles

- **Primary economical:** `google/gemini-2.5-flash-lite`.
- **Quality fallback:** `openai/gpt-4.1-mini`.
- **Optional free test:** `google/gemma-4-26b-a4b-it:free`.

ADR-020 is `PROPOSED`, not accepted. No model is inserted into active settings.
Qwen remains the first alternative if Spanish potential outweighs current
structured-output confidence.

## Deferred and rejected candidates

GPT-5 Mini, GPT-5.1, and Gemini 3.5 Flash Lite were penalized for reasoning or
frontier capability/cost irrelevant to this task. Mistral Small 3.1 lacked the
required public parameter metadata. Nemotron free was primarily positioned for
agentic reasoning. `openrouter/free` was rejected for nondeterministic routing.

## Free-model limitations

OpenRouter documents lower rate limits, changing availability, possible peak
latency, and variable free capacity. A named `:free` variant preserves the
model ID better than `openrouter/free`, but it does not guarantee stable
providers, uptime, performance, schema behavior, or permanent availability.
Free models are experiment-only.

## Why nothing is activated

The committed configuration remains:

```text
ORION_SCRIPTING_PROVIDER=simulated
ORION_SCRIPTING_MODEL=
ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS=false
```

No API key, account balance, generation response, free credit, or paid credit
was used. Prices and availability are dated snapshots. Public metadata cannot
prove Spanish creative quality.

## Controlled-test requirements and reevaluation

A later phase needs owner funding, a locally configured secret, exact model ID,
current privacy/ZDR/provider review, a Decimal cost ceiling, and explicit
authorization for a bounded fixture set. It should test 15/30/60-second Spanish
scripts for schema validity, hook quality, pacing, factual tone, scene
consistency, latency, and reported usage. No automatic fallback or paid retry is
permitted.

Reevaluate by 2026-09-01 or sooner if pricing, aliases, provider routes,
structured-output support, free availability, privacy policy, or ORION's schema
changes.
