# ADR-020: OpenRouter scripting model selection

## Status

PROPOSED

## Date

2026-08-01 UTC. Review no later than 2026-09-01 and immediately if a model,
price, endpoint, structured-output capability, free variant, or privacy policy
changes.

## Context

Phase 6C added a controlled OpenRouter adapter behind the existing SCRIPTING
port. It requires a deterministic model identity, strict JSON Schema, a durable
pre-submission checkpoint, Decimal cost authority, and no automatic retry after
an uncertain billable submission. The committed runtime remains simulated,
without a model, secret, balance, or spending authorization.

ORION needs short scripts in Latin American Spanish for 15, 30, and 60-second
videos. Hooks, concise narration, scene continuity, visual direction, stable
machine parsing, low cost, and low latency matter. Browsing, tools, image input,
large reasoning budgets, and enormous context do not.

The dated evidence and calculations are in
[`../PRODUCTION_OPENROUTER_SCRIPTING_MODEL_SELECTION.md`](../PRODUCTION_OPENROUTER_SCRIPTING_MODEL_SELECTION.md)
and
[`../research/openrouter-scripting-model-evaluation.json`](../research/openrouter-scripting-model-evaluation.json).

## Candidates and evidence

The public unauthenticated OpenRouter catalog returned 336 models. Applying
text input/output, current ID, public pricing, context, non-expiration, and
`response_format`/`structured_outputs` eligibility yielded 283 broad matches.
Four paid and two named free variants were shortlisted:

- `google/gemini-2.5-flash-lite`: five endpoint records, optional reasoning,
  very low list price, and public provider-specific structured-output
  observations.
- `openai/gpt-4.1-mini`: three endpoint records and the strongest published
  structured-output reliability observation in the shortlist, at higher cost.
- `qwen/qwen3-30b-a3b-instruct-2507`: five provider organizations, non-thinking
  instruction model, explicit multilingual positioning, and the lowest paid
  list-price estimate.
- `mistralai/mistral-small-3.2-24b-instruct`: three endpoint records, low cost,
  and official structured-output task improvements, but weaker Spanish and
  latency evidence.
- `google/gemma-4-26b-a4b-it:free`: named free test candidate with structured
  output and two free endpoints observed on its model page.
- `openai/gpt-oss-20b:free`: named free alternative with structured output,
  but mandatory reasoning and weaker free-tier availability evidence.

`openrouter/free` was rejected because random model selection conflicts with
request fingerprints, reproducibility, schema behavior, and recovery.

## Scoring

Weights are structured output 30%, Spanish potential 25%, cost 20%,
latency/efficiency 10%, availability/routing 10%, and context fit 5%. Scores are
0–10. Each weighted contribution is reduced by an evidence-confidence
adjustment and explicit risk penalty. This produces a decision aid, not a
quality benchmark.

Final results, rounded to one decimal for human use:

1. Gemini 2.5 Flash Lite — 7.6.
2. GPT-4.1 Mini — 7.3.
3. Qwen3 30B A3B Instruct 2507 — 7.2.
4. Mistral Small 3.2 24B — 6.3.
5. Gemma 4 26B A4B free — 5.3.
6. gpt-oss-20b free — 5.2.

The close top-three result is material. Current public metadata cannot prove
Spanish creative quality.

## Proposed decision

- **Primary economical candidate:** `google/gemini-2.5-flash-lite`.
- **Quality fallback candidate:** `openai/gpt-4.1-mini`.
- **Optional free test candidate:** `google/gemma-4-26b-a4b-it:free`.

These are controlled-test roles, not active runtime defaults. Gemini leads
because its current public evidence combines strict output support across five
endpoint records, current provider-specific structured-output observations,
optional reasoning, low cost, and strong latency potential. GPT-4.1 Mini is the
fallback because its structured-output evidence is stronger but its output
price is higher. Gemma is only a pre-funding experiment candidate.

Qwen remains the most important alternative: it is cheaper than Gemini and has
better explicit multilingual evidence, but lacks a current public
provider-specific structured-output reliability observation. A single real
test could change the ordering.

## Consequences

- No runtime setting changes.
- `ORION_SCRIPTING_PROVIDER=simulated` remains active.
- `ORION_SCRIPTING_MODEL` remains empty.
- Billable authorization remains false and cost limits remain empty.
- No key, account, balance, authenticated endpoint, free generation, or paid
  generation was used.
- A future test must use one exact model ID per durable fingerprint; no automatic
  cross-model fallback is allowed.
- Free variants remain unsuitable for permanent production assumptions because
  their limits, routes, performance, and availability can change.

## Risks and invalidation conditions

- Latin American Spanish quality, hook strength, pacing, factual tone, and scene
  consistency are untested.
- Structured-output observations are routed-provider snapshots, not ORION
  schema tests.
- ZDR filtering can reduce endpoint availability.
- Prices exclude future price changes and credit-purchase fees.
- Model aliases and free variants can move or disappear.

Reevaluate on any relevant metadata change, by 2026-09-01, or after the first
controlled test. A test that shows schema failure, poor Spanish, excessive
latency, or weak scene consistency invalidates the proposed role.

## Required quality test

A later phase must fund the owner-controlled account, select an exact test
model, configure the secret only locally, approve a Decimal ceiling, verify
current privacy and pricing, submit exactly one durable 15/30/60-second fixture
set, and score schema validity plus blind Spanish quality. The test needs its
own explicit authorization. This ADR authorizes no request or spend.
