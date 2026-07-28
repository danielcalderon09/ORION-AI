# Production TTS Listening-Test Future Execution Runbook

Status: procedure only; no step executed by Phase 5G.3B

## 1. Purpose

This runbook defines how a later, separately authorized ORION TTS listening
test could be executed without weakening Phase 5G.2 billing and recovery
controls. It is not an executable script and contains no provider request,
credential command, endpoint, or account instruction.

## 2. Offline preparation status

Phase 5G.3B produced only public-safe scripts, blocked plan records, empty
templates, pure calculations, validators, tests, and documentation. No audio
was generated or processed.

## 3. Prerequisites

Before scheduling a run, require:

- a clean, reviewed repository baseline;
- valid Phase 5G.3 research and Phase 5G.3B hashes;
- exact candidate model and voice;
- exact region;
- exact current pricing;
- approved privacy, retention, commercial, content, and disclosure posture;
- a separately accepted execution plan;
- a minimal-scope credential handling design outside Git;
- an uncommitted authorization within USD 10;
- at least five eligible evaluators, including three with strong
  Colombian-Spanish familiarity.

## 4. Research freshness check

Run both offline validators. Stop if `review_after` has passed, the provider
changed a model/voice/price/term, or any research hash differs. Refresh official
research and review the diff before proceeding.

## 5. Exact price verification

Verify the exact chosen model, voice, billing unit, currency, region, included
features, timing charges, minimum commitments, and overages from current
official material. Record a dated pricing snapshot. Unknown pricing blocks the
run.

## 6. Candidate eligibility review

For each slot, resolve model, voice, locale, generation mode, output format,
native rate, region, and availability. A slot remains blocked until all
mandatory blockers are removed by reviewed evidence.

Do not add a provider simply because it has free credit or an existing account
balance.

## 7. Terms, privacy, and commercial approval

Approve the exact selected service/model for input rights, output rights,
commercial use, retention, training/data use, regional processing, deletion,
disclosure, prohibited content, and incident handling. Record role-based
approval without personal approver data in the plan.

## 8. Create an uncommitted authorization

Copy the authorization template into an ignored, access-controlled execution
workspace outside the repository. Never commit it.

Populate only after review:

- authorized candidate IDs;
- verified USD price timestamp;
- unit, candidate, and total ceilings;
- authorization and expiry timestamps;
- role and approval reference;
- execution environment;
- all approval flags.

The validator must reject the repository template if any of these look live.

## 9. Budget calculation

Use Decimal values. Calculate every unit's maximum cost, each candidate's
worst-case total, and the overall worst case.

Require:

- unit cost ≤ unit ceiling;
- candidate total ≤ candidate ceiling;
- overall total ≤ authorized total;
- authorized total ≤ USD 10.

Free credit, subscription allowance, and account balance do not authorize
spend. Do not convert currency without a separately approved conversion
snapshot.

## 10. Credential handling expectations

Credentials must be:

- created only after provider selection for the test;
- minimally scoped to the exact required operation;
- stored in an approved secret mechanism outside Git;
- unavailable to evaluators and documentation;
- rotated or revoked after the bounded run;
- excluded from logs, manifests, errors, and shell history.

This runbook intentionally provides no credential command.

## 11. Plan hash verification

Verify research, candidate, script, generation-plan, and composite plan hashes.
Any mismatch invalidates authorization. Do not “fix” a hash in place without
regenerating the dependent artifacts and repeating review.

## 12. Generation-unit preparation

Expand only the reviewed candidate × fixed-script matrix. Resolve model, voice,
pricing snapshot, capability snapshot, and expected output without changing the
script text. Build a provider-neutral fingerprint for each unit.

Do not put narration text, credentials, provider request bodies, or temporary
download locations into research plan records.

## 13. Mandatory durable pre-submission checkpoint

Before a potentially billable call, create the Phase 5G.2 durable remote record
as `prepared`, bind the exact fingerprint, estimate, authorization, capability
snapshot, pricing snapshot, output expectation, and permitted submission
state. Checkpoint `submitting` immediately before the call.

## 14. No-retry rule

Make one submission attempt. If the outcome is ambiguous after bytes may have
been sent, mark the record `uncertain`. Do not resubmit, switch provider, or
create a fresh attempt automatically. Resolve deliberately using a durable
provider identity when one exists.

## 15. Audio validation

Validate returned bytes before any evaluator packaging:

- expected container/encoding;
- sample rate, channels, and sample width;
- non-empty duration within bounds;
- checksum and size;
- no truncation or unexpected trailing data;
- text/output provenance;
- no unsafe metadata.

An invalid output is an incident, not an evaluator sample.

## 16. Preserve original output

Store verified original provider output separately and immutably with checksum,
safe provenance, and access controls. Do not overwrite it with normalized
comparison audio.

## 17. Normalization procedure

Create a comparison copy using one reviewed, deterministic offline media path:

- mono 24 kHz, 16-bit WAV PCM;
- `-16.0 LUFS` integrated target under ITU-R BS.1770-4;
- `-1.0 dBTP` ceiling;
- 250 ms leading and trailing silence;
- no changes to internal pauses;
- no enhancement, denoising, provider EQ, or prosody correction.

Record tool version and output checksum. Phase 5G.3B implements no processor and
invokes no FFmpeg.

## 18. Blind-ID generation

Supply a fresh external random HMAC-SHA-256 key of at least 32 bytes. Do not use
the test fixture key. Derive evaluator-specific opaque IDs and order from the
run, evaluator, candidate, and sample identities.

Never print or persist the key in the repository.

## 19. Evaluator packages

Build one package per pseudonymous evaluator. Include only opaque sample names,
instructions, the scorecard, and the evaluator-specific order. Strip provider
metadata and audio tags. Keep the decoding map separate from packages and Git.

## 20. Evaluation instructions

Ask evaluators to use a consistent device in a quiet environment, avoid
provider identification, avoid score discussion, score every clip independently,
complete forced choices, and report defects by blind ID.

## 21. Scorecard ingestion

Accept only completed, canonical scorecards with:

- a unique scorecard and evaluator ID;
- valid consent confirmations;
- required task-relevant metadata;
- one 1–5 score per category per blind sample;
- one forced choice per script sample;
- valid confidence and bounded comments;
- matching plan and normalization hashes.

Reject partial, duplicate, unknown-ID, or deblinded submissions.

## 22. Aggregation

After the minimum listener threshold, join scorecards to the external decoding
map in a restricted analysis workspace. Calculate category medians, secondary
means, per-sample medians, weighted score, IQR, forced-choice wins, and critical
failure counts.

Do not expose provider identities in evaluator-facing output.

## 23. Critical-failure review

Apply the predeclared thresholds before revealing providers. Review every hard
incident separately. A disqualified candidate cannot be restored by its
average score. Do not tune thresholds after unblinding.

## 24. Provider decision procedure

Separate:

1. descriptive listener results;
2. product judgment about cost, privacy, operations, and implementation;
3. provider selection.

Results do not automatically select a provider. A near tie, low confidence,
material spread, or unresolved risk may correctly produce no selection.

## 25. Accepted ADR requirement

Selecting a provider requires a new accepted ADR naming the exact
provider/model/voice strategy, initial format/lifecycle, costs, region,
retention posture, disclosure, recovery rules, and conditions for review.
ADR-019 remains Deferred until then.

## 26. Cleanup and retention

After the run:

- revoke or rotate the bounded credential;
- reconcile every durable request and reported cost;
- retain originals and normalized copies only for the approved period;
- delete temporary packages and expired download material;
- protect the decoding map according to the approved retention policy;
- archive only safe, pseudonymous descriptive results;
- never commit live authorization or secrets.

## 27. Incident response

Stop immediately for ambiguous submission, unexpected charge, compromised
credential, provider/model drift, unsafe substitution, output corruption,
region/voice loss, or terms/privacy mismatch. Preserve durable evidence,
prevent resubmission, reconcile cost, and require explicit review before any
continuation.

Do not automatically fall back to another provider.

## 28. Phase 5G.3B confirmation

This phase executed none of the future steps above. It made no network, cloud,
upload, generation, audio-processing, authenticated, or billable request. It
created no account, credential, endpoint, or real authorization. Simulated
speech remains active; billable speech remains false; the remote provider
remains disabled; no provider is selected.
