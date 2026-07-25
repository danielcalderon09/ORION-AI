# Image Acquisition durable (Fase 5E.2)

## Propósito y alcance

`ACQUIRING_ASSETS` consume exclusivamente el `ProductionVisualAssetPlan` durable y materializa
solo assets obligatorios `still_image` + `text_to_image`. Produce imágenes PNG, JPEG o WebP bajo
el almacén binario de Production y un checkpoint:

```text
production/<job_id>/acquiring_assets/attempt-<n>/image-acquisition-manifest.json
production/<job_id>/assets/images/image-<visual_asset_id>.<ext>
production/<job_id>/assets/images/image-<visual_asset_id>.<ext>.asset.json
```

No usa el prompt original, ProductionPlan, ProductionScript ni ProductionScenePlan. No admite
image-to-image, reference images, URLs, descargas, video, audio, timeline o render.

## Reader, contratos y checkpoints

`DurableProductionVisualAssetPlanReader` selecciona solo `production_visual_asset_plan` del mismo
job. Prioriza `input_artifact_ids` y usa attempt, `created_at` y artifact ID para el fallback
determinista. Verifica ruta exacta, confinement, links/junctions, archivo regular, límite, tamaño,
SHA-256, UTF-8, JSON estándar sin constantes ni claves duplicadas, schema y versión.

`ProductionImageAcquisitionManifest` y sus entries son Pydantic frozen con `extra=forbid`. Cada
entry pasa por `pending -> generating -> stored`; también existen fallos permanent/transient y
`uncertain`. El writer crea, actualiza y finaliza el mismo manifest mediante compare-and-swap,
temporal local, `flush`, `fsync` y `os.replace`.

Una entry `stored` solo se recupera tras validar sidecar, bytes, hash, tamaño, MIME, dimensiones,
scene/shot y procedencia. Una entry `generating` sin binario verificable después de restart pasa a
`uncertain`: no se repite automáticamente una llamada potencialmente facturable. Retry explícito
crea otro attempt y puede reutilizar binarios cuya procedencia coincida exactamente.

## Providers

`SimulatedImageAcquisitionProvider` es el default, offline y gratuito. Usa Pillow únicamente
dentro del adaptador para producir una imagen raster válida y determinista por
`visual_asset_id`; no intenta representar contenido creativo real.

`OpenRouterImageAcquisitionProvider` es lazy y usa la Images API dedicada auditada el
2026-07-23:

```text
POST https://openrouter.ai/api/v1/images
```

Envía modelo explícito, prompt construido solo desde el visual asset aprobado, `n=1`, `size`,
`aspect_ratio`, quality, formato, `stream=false` y `provider.allow_fallbacks=false`.
`provider.only` es una opción global cerrada. No consulta catálogo, no usa chat completions, no
hace fallback y no descarga URLs.

La respuesta debe contener exactamente un `data[].b64_json`. Base64 se decodifica estrictamente,
sin whitespace, data URL ni reparación, con límites antes y después. `media_type`, usage, cost,
request ID, reported model y finish reason son opcionales. Los bytes pasan siempre por
`BinaryAssetWriter`, que inspecciona el formato real y rechaza SVG, HTML, corrupción, MIME
inconsistente, dimensiones inesperadas y decompression bombs.

## Configuración

```text
ORION_IMAGE_ACQUISITION_PROVIDER=simulated
ORION_IMAGE_ACQUISITION_MODEL=
ORION_IMAGE_ACQUISITION_API_KEY=
ORION_IMAGE_ACQUISITION_BASE_URL=https://openrouter.ai/api/v1
ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS=120
ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS=2
ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS=1.0
ORION_IMAGE_ACQUISITION_OUTPUT_FORMAT=png
ORION_IMAGE_ACQUISITION_QUALITY=auto
ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES=40000000
ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES=25000000
ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES=8000000
ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES=4000000
ORION_IMAGE_ACQUISITION_PROVIDER_ONLY=
ORION_OPENROUTER_HTTP_REFERER=
ORION_OPENROUTER_APP_TITLE=
```

Con `openrouter`, model y key son obligatorios, la URL debe ser HTTPS sin credenciales embebidas
y el slug de routing se valida. Provider, model, keys, routing, headers, URL, timeout, retries y
formato no se aceptan por job. Con Production apagado no se construye container, cliente ni task.

## Errores, coste y seguridad

Timeout, conexión, 408/409 temporal/425/429/5xx son retryables. Auth, policy, model no
encontrado, parámetros/formato/tamaño inválidos y respuestas contractualmente inválidas son
permanentes. Los mensajes externos completos nunca salen del adaptador. `CancelledError` se
propaga.

El coste solo se conserva cuando OpenRouter lo reporta y se representa como decimal; no existen
precios hardcodeados. Logs, StageResult, artifacts, manifest y API nunca contienen prompt
completo, bytes, base64, Authorization, API key, headers, payload externo o ruta absoluta.

Todas las pruebas del provider real usan `httpx.MockTransport`; no hacen red ni generan costes.
Rollback consiste en volver a `ORION_IMAGE_ACQUISITION_PROVIDER=simulated`.

Fase 5F.1 consume ahora este manifest y sus imágenes verificadas para crear clips MP4 simulados.
Image Acquisition conserva sus contratos públicos y allowlists sin cambios. Consulta
`PRODUCTION_VIDEO_CLIP_GENERATION.md`.

La integración OpenRouter de Fase 5F.2 queda desactivada hasta disponer de un publisher real.
# Integración posterior de Fase 5F.2

OpenRouter video consume exclusivamente la imagen verificada por este manifest,
pero no expone el BinaryAssetStore. Un puerto separado exige publicación HTTPS
segura; su implementación productiva permanece desactivada. La siguiente fase
es Fase 5F.3 — Secure Public Frame Publishing and Controlled Live Validation.
