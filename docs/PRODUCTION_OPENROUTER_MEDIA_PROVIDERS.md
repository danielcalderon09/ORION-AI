# OpenRouter media providers — Phase 6D

Phase 6D activates no provider by default and performed no live generation. It adds controlled
OpenRouter implementations behind ORION's existing image acquisition and speech generation
boundaries. Video and music remain configuration vocabulary only.

## Fixed model map

| Role | Model | Runtime state |
|---|---|---|
| Script | `google/gemini-2.5-flash-lite` | proven opt-in OpenRouter |
| Image primary | `google/gemini-3.1-flash-lite-image` | implemented, opt-in |
| Image quality fallback | `google/gemini-3.1-flash-image` | vocabulary only |
| TTS primary | `hexgrad/kokoro-82m` | implemented, opt-in |
| Video primary | `google/veo-3.1-lite` | future, simulated now |
| Video alternative | `bytedance/seedance-2.0` | future, simulated now |
| Music | `google/lyria-3-clip-preview` | future, simulated now |

## Current official interfaces

Images use the dedicated `POST /api/v1/images` endpoint documented by
[OpenRouter Image Generation](https://openrouter.ai/docs/guides/overview/multimodal/image-generation).
ORION requests exactly one embedded image, non-streaming, at `1K` with the durable plan's
`9:16`, `16:9`, or `1:1` aspect ratio. The provider returns bounded base64; ORION validates the
declared MIME, actual signature, decodability, dimensions, aspect ratio, byte size, and checksum
before registering the existing `SOURCE_IMAGE` artifact. URLs and active/vector payloads are
rejected.

The official [Gemini 3.1 Flash Lite Image model page](https://openrouter.ai/google/gemini-3.1-flash-lite-image/pricing)
publishes token-based input/output pricing. ORION does not convert that catalog value into a
guaranteed per-image price because output token usage can vary; activation therefore requires an
explicit local Decimal estimate and job ceiling.

Kokoro uses `POST /api/v1/audio/speech`, following the official
[OpenRouter TTS guide](https://openrouter.ai/docs/guides/overview/multimodal/tts). ORION requests
raw PCM and wraps the bounded 24 kHz, mono, 16-bit response into the existing canonical WAV
artifact. It retains a sanitized `X-Generation-Id` when present. OpenRouter's current Kokoro
catalog confirms Spanish and 54 preset voices, but does not publicly enumerate the exact accepted
Spanish voice IDs. Therefore `ORION_SPEECH_GENERATION_REMOTE_VOICE` is required and no
unverified voice is hard-coded.
The official [Kokoro model page](https://openrouter.ai/hexgrad/kokoro-82m/pricing) currently lists
character-based pricing; the runtime still uses explicit local authorization rather than fetching
or trusting mutable catalog metadata.

Video remains simulated. The future API is asynchronous `POST /api/v1/videos`, as documented by
[OpenRouter Video Generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation).
Current official metadata for Veo 3.1 Lite includes portrait `9:16`, landscape `16:9`, and 4, 6,
or 8 second clips. Phase 6D does not submit, poll, or download a video.

Music remains simulated. `google/lyria-3-clip-preview` is recorded for future 30-second music
clips; Phase 6D adds no music transport.

## Billable safety and durable recovery

Both live adapters require provider selection, a configured credential and model, explicit
`ALLOW_BILLABLE_REQUESTS=true`, a Decimal estimate, an authorized maximum, and a request limit.
No runtime price is invented: the owner explicitly authorizes the estimate. Image authorization
covers the estimated request cost multiplied by `MAX_REQUESTS_PER_JOB`; speech applies the same
job-level ceiling.

One image asset and one narration segment each have an independent durable request identity.
The lifecycle is `prepared -> submitting -> completed|failed|uncertain`. A checkpoint is written
before transmission and `fresh_submission_permitted` becomes false before the POST. Timeout,
cancellation, or a connection failure after submission becomes `uncertain` and is never sent
again automatically. Completed local image/WAV artifacts are reused on resume.

Image request state is held in the existing acquisition manifest. Speech uses the existing
`remote-speech-jobs/segment-*.json` record and its reconciliation boundary. Neither record stores
the API key, Authorization header, raw provider body, complete base64, complete PCM, prompt text,
cookies, or absolute filesystem paths.

## Shared credential

`ORION_OPENROUTER_API_KEY` is the canonical optional local secret. Scripting remains backward
compatible with `ORION_SCRIPTING_API_KEY`; image remains backward compatible with
`ORION_IMAGE_ACQUISITION_API_KEY`. Image and speech can reuse the canonical or scripting key
internally, so the owner need not duplicate it. Secrets are never exposed in provider status.

## Defaults, tests, and limitations

Committed defaults remain image `simulated`, speech `simulated`, video `simulated`, and music
`simulated`. Desktop/backend provider status is local settings-derived and performs no startup
probe. All provider tests inject `httpx.MockTransport`; no test contacts OpenRouter.

Current limitations:

- no verified public OpenRouter Spanish voice ID is hard-coded;
- image reference inputs are fingerprint-ready but not sent in this MVP;
- image quality fallback is not automatic;
- no video or music execution is activated;
- image and speech cost estimates must be supplied explicitly by the owner;
- an uncertain request requires manual review and a new controlled request identity.

## Later manual smoke tests

These commands are intentionally not run by the test suite. Keep every unrelated creative
provider simulated and use one planned scene so each command can transmit at most one media
request.

For one image, authorize an explicit estimate of `0.005` USD with a job ceiling of `0.01` USD,
set `ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB=1`, and run:

```powershell
python -m backend.src.production.cli.generate_video --prompt "Crea una imagen cinematografica vertical de Marte al amanecer." --target-duration 15 --aspect-ratio 9:16 --scene-count 1 --output-summary
```

For one Spanish narration, first set a separately verified OpenRouter-compatible Spanish Kokoro
voice ID. Authorize `0.001` USD for both the estimate and job ceiling, set
`ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB=1`, and run:

```powershell
python -m backend.src.production.cli.generate_video --prompt "Explica en 15 segundos una curiosidad sorprendente sobre Marte." --target-duration 15 --aspect-ratio 9:16 --scene-count 1 --output-summary
```
