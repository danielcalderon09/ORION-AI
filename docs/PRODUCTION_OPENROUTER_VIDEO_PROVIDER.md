# OpenRouter Veo image-to-video provider — Phase 6F.1

Phase 6F.1 integrates OpenRouter video generation behind ORION's existing
`video_clip_generation` boundary. The committed default remains simulated and
billable video is disabled. Development and regression tests use only fake
HTTP transports.

## Official contract verified on 2026-08-08

Primary sources:

- [OpenRouter video generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)
- [OpenRouter image-to-video cookbook](https://openrouter.ai/docs/cookbook/video-generation/image-to-video)
- [Veo 3.1 Lite](https://openrouter.ai/google/veo-3.1-lite)

ORION uses these official endpoints:

```text
GET  https://openrouter.ai/api/v1/videos/models
POST https://openrouter.ai/api/v1/videos
GET  https://openrouter.ai/api/v1/videos/{id}
GET  https://openrouter.ai/api/v1/videos/{id}/content?index=0
```

Submission is asynchronous and must return HTTP 202 with `id`, `polling_url`
and `status`; `generation_id` is optional. Polling accepts the closed states
`pending`, `in_progress`, `completed`, `failed`, `cancelled`, and `expired`.
ORION ignores provider output URLs and downloads through the official content
endpoint constructed from the validated remote ID.

The image-to-video payload is:

```json
{
  "model": "google/veo-3.1-lite",
  "prompt": "<bounded motion prompt>",
  "duration": 4,
  "resolution": "720p",
  "aspect_ratio": "9:16",
  "generate_audio": false,
  "frame_images": [
    {
      "type": "image_url",
      "image_url": {"url": "https://<public-host>/<verified-frame>"},
      "frame_type": "first_frame"
    }
  ]
}
```

OpenRouter's current cookbook documents a directly downloadable public HTTPS
URL for `frame_images`; it does not document local paths, `file://`, multipart,
or data URLs for this endpoint. ORION therefore republishes the already
verified `SOURCE_IMAGE` bytes through the existing asset-publishing boundary.
The filesystem publisher only copies bytes into a configured publication root;
the owner must map that root to the configured public HTTPS base URL. ORION
does not create a tunnel, web server, or cloud bucket.

Veo 3.1 Lite advertises 4, 6, and 8 second output, 720p or 1080p, and `9:16`
or `16:9`. ORION v1 fixes generated audio to `false`; narration remains Kokoro
and music remains a separate stage. The first controlled test is 4 seconds,
720p, portrait, and one source image.

## Durable lifecycle and recovery

Each visual asset has one record at:

```text
production/<job_id>/generating_video_clips/attempt-<n>/remote-jobs/video-<visual_asset_id>.json
```

Lifecycle:

```text
prepared -> submitting -> submitted -> polling -> completed
                         \-> failed
                         \-> uncertain
```

`prepared` is persisted before the POST. Immediately before transmission the
record becomes `submitting` and `fresh_submission_permitted=false`. A timeout,
cancellation, connection loss, malformed 202, or failed accepted-request
checkpoint becomes `uncertain`; it is never submitted again automatically.
Once a remote ID exists, resume polls the same job. Polling attempts do not
count as paid submissions. Completed local clips are reused.

The record retains provider/model, source image SHA-256, prompt SHA-256,
capability snapshot hash, request fingerprint, publication identity/expiry,
requested duration/resolution/aspect ratio/audio flag, submission HTTP status,
safe remote ID/status, polling counters, timestamps, estimated cost, reported
cost when supplied, and reported model when supplied.

It never retains API keys, Authorization, prompt text, source-image bytes or
base64, absolute paths, raw provider bodies, complete headers, cookies, signed
output URLs, or query tokens.

The request fingerprint includes provider/model, source image SHA-256, prompt
hash, duration, resolution, aspect ratio, audio flag, capability snapshot, and
request schema/configuration version. It excludes timestamps, attempts,
credentials, public/signed URLs, and machine paths.

## Billing and output safety

Activation requires all of:

- provider `openrouter` and explicit model;
- a canonical or backward-compatible OpenRouter credential;
- `ALLOW_BILLABLE_REQUESTS=true`;
- capability metadata that exactly supports model/duration/resolution/ratio;
- a reproducible provider pricing SKU;
- estimated cost at or below the configured Decimal maximum;
- `MAX_REQUESTS_PER_JOB=1` for the first test;
- a real filesystem publication boundary backed by public HTTPS.

ORION makes at most one POST per durable request. Polling is independently
bounded by attempt count and overall time. Unknown remote states fail closed.

The download uses fixed OpenRouter host/path, no redirects, bounded streaming,
`video/mp4`, and an MP4 signature check. The existing binary store and ffprobe
then require one H.264 video stream, no audio or extra streams, contractual
dimensions/duration/frame rate, bounded bytes, and a checksum before storing
the existing `VIDEO_CLIP` artifact. Media composition and rendering consume
that artifact without a provider-specific path.

## Configuration and future models

Safe committed defaults:

```text
ORION_VIDEO_CLIP_GENERATION_PROVIDER=simulated
ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS=false
ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER=disabled
ORION_ASSET_PUBLISHING_PUBLISHER=null
```

Primary: `google/veo-3.1-lite`.

Future candidates only, with no fallback execution:

- fast: `bytedance/seedance-2.0-fast`;
- quality: `bytedance/seedance-2.0`.

Known limitations: public HTTPS hosting is external to ORION; no text-to-video,
last frame, multiple references, provider fallback, provider audio, webhooks,
or remote cancellation is implemented. Video Generation is not advertised by
OpenRouter as Zero Data Retention eligible.
