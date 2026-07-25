# OpenRouter Asynchronous Video Provider (Fase 5F.2)

## Auditoría oficial

Auditoría realizada el **2026-07-24 UTC** contra la guía, API reference y
cookbook oficiales de OpenRouter Video Generation.

Fuentes primarias auditadas:

- `https://openrouter.ai/docs/guides/overview/multimodal/video-generation`
- `https://openrouter.ai/docs/api/api-reference/video-generation/submit-a-video-generation-request`
- `https://openrouter.ai/docs/api/api-reference/video-generation/list-all-video-generation-models`
- `https://openrouter.ai/docs/api/api-reference/video-generation/poll-video-generation-status`
- `https://openrouter.ai/docs/api/api-reference/video-generation/download-generated-video-content`
- `https://openrouter.ai/docs/cookbook/video-generation/image-to-video`
- `https://openrouter.ai/docs/guides/privacy/provider-logging`

Endpoints confirmados:

```text
GET  https://openrouter.ai/api/v1/videos/models
POST https://openrouter.ai/api/v1/videos
GET  https://openrouter.ai/api/v1/videos/{job_id}
GET  https://openrouter.ai/api/v1/videos/{job_id}/content?index=0
```

Submit devuelve HTTP 202 con `id`, `polling_url`, `status` y
`generation_id` opcional. Polling añade `unsigned_urls`, `error`,
`generation_id` y `usage.cost/is_byok`. Los estados oficiales son `pending`,
`in_progress`, `completed`, `failed`, `cancelled` y `expired`.

El catálogo confirma `id`, `canonical_slug`, `supported_durations`,
`supported_resolutions`, `supported_aspect_ratios`, `supported_sizes`,
`supported_frame_images`, `generate_audio`, `seed`,
`allowed_passthrough_parameters` y `pricing_skus`. ORION valida exactamente el
modelo configurado; no selecciona fallback ni cambia duración, resolución o
aspect ratio.

Image-to-video usa `frame_images` con `type=image_url`, `image_url.url` y
`frame_type=first_frame`. La URL debe ser HTTPS pública, estable y directamente
descargable. ORION envía `generate_audio=false` y no envía nulls,
`input_references`, `last_frame`, `callback_url`, `provider`, seed ni
passthrough.

No están confirmados un endpoint de cancelación, el TTL exacto de jobs/URLs ni
una unidad universal para todos los `pricing_skus`. ORION no inventa esos
contratos. Solo estima coste con `per-video-second` o
`per-video-second-<resolution>`; un SKU ambiguo como `generate` falla cerrado.
403, 408, 409 y 422 no figuran en la tabla vigente de submit, aunque el
clasificador los maneja defensivamente sin reintentar el POST.

Los webhooks están documentados, pero no se implementan. Video Generation no
es elegible para Zero Data Retention: el resultado asíncrono se conserva
temporalmente para permitir su descarga.

## Arquitectura y flujo durable

`OpenRouterVideoClipGenerationProvider` implementa el puerto existente. Handler,
store MP4, ffprobe, artifacts y manifest permanecen provider-neutral.

```text
SOURCE_IMAGE verificada
  -> VideoMotionPromptBuilder
  -> VideoFrameImagePublisher
  -> GET /videos/models (cache TTL)
  -> BillableVideoGenerationPolicy
  -> POST /videos (una sola vez)
  -> remote-jobs/video-<visual_asset_id>.json
  -> polling acotado/checkpointado
  -> GET /videos/{id}/content?index=0
  -> VideoClipBinaryStore + ffprobe
  -> manifest/artifacts existentes
```

El remote job se guarda en:

```text
production/<job_id>/generating_video_clips/attempt-<n>/
remote-jobs/video-<visual_asset_id>.json
```

Es JSON canónico write-once/CAS. Persiste IDs/estado remotos, intentos,
timestamps, prompt hash, capability snapshot hash, fingerprint, publication
ID, coste estimado/reportado y SKU. No guarda API key, Authorization, URL
completa, signed URL, query string, body remoto ni bytes.

## Publisher, prompt y privacidad

`VideoFrameImagePublisher` separa publicación y generación. Esta fase incluye
`DisabledVideoFrameImagePublisher` (falla cerrado) e
`InMemoryVideoFrameImagePublisher` (solo tests, HTTPS ficticio, cero red). No
existe publisher real, object storage, CDN, túnel o endpoint público.

No se expone el workspace ni se aceptan URLs por job. Se rechazan HTTP,
localhost, IP privadas/link-local, userinfo y fragmentos. Una publicación
expirada antes del submit no se usa; después del submit recovery utiliza solo
el remote job ID y nunca republica ni vuelve a cobrar.

`VideoMotionPromptBuilder` crea un prompt cerrado y reproducible desde el rol
durable allowlisted. Preserva identidad/composición/colores/entorno, pide
movimiento sutil y prohíbe nuevos sujetos, texto, logos, cortes, transiciones y
audio. El texto se excluye de repr/serialización/logs; solo persiste SHA-256.
La adquisición actual no conserva descripción visual completa, por lo que no
se releen Prompt, Plan, Script, Scene Plan ni Visual Asset Plan.

## Coste, configuración y opt-in

El default continúa siendo:

```text
ORION_VIDEO_CLIP_GENERATION_PROVIDER=simulated
ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS=false
ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER=disabled
```

Antes del POST, la policy exige opt-in, OpenRouter, capacidades exactas, una
salida, ausencia de clip/remote job, lease/CAS, precio estimable y coste bajo el
máximo. Dinero usa `Decimal`; la estimación no promete coincidir con el cargo.
Aunque exista una key, OpenRouter no se activa automáticamente. Como no hay
publisher real, la composición soportada no puede enviar solicitudes
facturables en esta fase.

## HTTP, descarga y validación

El adaptador usa `httpx.AsyncClient`, TLS, host oficial fijado, redirects
desactivados, headers cerrados, timeouts y límites. Rechaza host externo,
userinfo, traversal, fragmentos y query tokens. Ignora `unsigned_urls` y
descarga siempre por el endpoint oficial construido desde un ID validado.

La descarga es streaming, exige `video/mp4`, limita bytes incrementalmente y
calcula SHA-256. El handler usa el store durable y ffprobe vuelve a exigir
MP4/H.264, un video stream, cero audio/subtitles/data, dimensiones, duración,
fps y frame count contractuales. No transcodifica resultados reales.

## Recovery, incertidumbre y cancelación

- Clip válido: cero publicación, discovery, submit, polling o descarga.
- Remote pending/in_progress: retoma polling sin repetir POST.
- Remote completed: descarga sin submit.
- Un attempt nuevo busca de forma contractual el último remote job activo o
  completed y lo reutiliza solo si coincide el fingerprint completo.
- Remote failed/cancelled/expired: fallo tipado; no reenvía en el attempt.
- Timeout/transporte tras posible envío: `uncertain`; no reenvía.
- 202 aceptado cuyo checkpoint falla: `uncertain`; evitar doble cobro tiene
  prioridad.
- `CancelledError` se propaga y se conserva el último checkpoint durable.

Polling usa reloj monotónico, máximos de tiempo/intentos, sleeper/jitter
inyectables, `Retry-After` acotado y checkpoints. Tests no duermen realmente.

## API, tests, rollback y límites

No hay endpoints nuevos. La API nunca expone URLs, firmas, Authorization, key,
prompt completo, bodies ni bytes. Todos los tests usan `httpx.MockTransport`,
hosts `.test`, key ficticia y guard de host; no existe live smoke.

Rollback: seleccionar `simulated`, mantener billable `false` y publisher
`disabled`. Manifests históricos 1.0.0 siguen válidos: campos remotos son
aditivos y opcionales.

No incluye publisher real, S3, R2, GCS, Azure Blob, CDN, live generation,
text-to-video, last frame, múltiples referencias, `input_references`, audio,
webhooks, fallback, selección automática, narración, música, timeline,
DaVinci, render final ni frontend.

La siguiente fase recomendada es **Fase 5F.3 — Secure Public Frame Publishing
and Controlled Live Validation**.
