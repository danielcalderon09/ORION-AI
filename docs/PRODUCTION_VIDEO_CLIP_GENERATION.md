# Generación durable de clips de video (Fase 5F.1)

## Alcance y pipeline

`GENERATING_VIDEO_CLIPS` se ejecuta inmediatamente después de `ACQUIRING_ASSETS` y antes de
narración, música, subtítulos, timeline o render:

```text
PLANNING -> SCRIPTING -> SCENE_PLANNING -> VISUAL_ASSET_PLANNING
-> ACQUIRING_ASSETS -> GENERATING_VIDEO_CLIPS -> GENERATING_NARRATION
```

La única entrada creativa y binaria es un `ProductionImageAcquisitionManifest` `completed` y los
artifacts `SOURCE_IMAGE` que referencia. El reader durable prioriza `input_artifact_ids` y usa un
fallback determinista por attempt, `created_at` y artifact ID. Verifica job, tipo, ruta contractual,
confinamiento, links/junctions/hard links, tamaño, SHA-256, UTF-8, JSON estricto, schema, estado,
sidecar, imagen, MIME, dimensiones, scene/shot y procedencia. No consulta prompt, plan, script,
scene plan, visual asset plan directo, rutas cliente ni URLs.

La salida es exactamente un MP4 por visual asset:

```text
production/<job_id>/assets/video-clips/video-<visual_asset_id>.mp4
production/<job_id>/assets/video-clips/video-<visual_asset_id>.mp4.asset.json
production/<job_id>/generating_video_clips/attempt-<n>/video-clip-generation-manifest.json
```

Los artifacts registrados son `SOURCE_VIDEO_CLIP` y
`PRODUCTION_VIDEO_CLIP_MANIFEST`. La API existente solo lista su metadata; no sirve MP4, bytes,
base64, `file://`, rutas absolutas ni comandos.

## Provider simulado y determinismo

`SimulatedVideoClipGenerationProvider` sigue siendo el provider predeterminado. Opera offline, no
importa HTTP, no usa API keys y no genera contenido creativo nuevo. Repite la imagen y superpone
un movimiento geométrico horizontal ligero cuyo color y dirección derivan de
`visual_asset_id`. Usa H.264, MP4, 24 o 30 fps, conserva dimensiones, elimina metadata variable,
fija un thread y no crea audio.

En el entorno validado, dos ejecuciones con la misma imagen, ID y configuración producen bytes y
SHA-256 idénticos. El fingerprint de configuración y la procedencia quedan en sidecar, manifest y
artifact. Distinto `visual_asset_id` produce contenido diferente.

## FFmpeg, ffprobe y seguridad

Se requieren ejecutables `ffmpeg` y `ffprobe` disponibles en `PATH` o configurados globalmente.
No se descargan, prueban ni ejecutan durante import, composición o startup. Si faltan, el handler
devuelve un error de dependencia tipado; nunca inventa un MP4.

Los adapters usan `asyncio.create_subprocess_exec` con argumentos cerrados, sin shell, URLs,
protocolos remotos, argumentos passthrough ni filtros de jobs. Aplican timeout, stdout/stderr
acotados, temporales privados y limpieza en `finally`. La cancelación termina el proceso con
terminate/kill controlado y propaga `CancelledError`. Los errores públicos no incluyen comandos,
entorno ni rutas absolutas.

`VideoClipIntegrityValidator` usa ffprobe después de codificar y en cada lectura durable. Exige
contenedor MP4, una pista H.264, cero audio, cero streams extra, cero attachments y capítulos,
dimensiones exactas, fps configurado, duración con tolerancia de 80 ms, frame count razonable,
tamaño máximo y checksum. Rechaza HTML, XML, SVG, ejecutables, imágenes renombradas, MP4 truncado
o corrupto, audio, codecs no permitidos y múltiples pistas.

## Store, manifest, checkpoints y recovery

El store de imágenes no se generalizó: sus allowlists, ruta y validación Pillow siguen dedicadas
a imágenes. `FilesystemVideoClipBinaryStore` es paralelo y especializado. Es write-once, valida
antes y después de escribir, usa lock exclusivo por asset, temporal en el mismo directorio,
`flush`, `fsync`, `os.replace`, sidecar canónico y lectura verificada. Rechaza traversal,
symlinks, junctions y hard links, y nunca sobrescribe una pareja incompatible.

El manifest usa JSON canónico UTF-8, claves ordenadas, `allow_nan=False`, compare-and-swap y
transiciones cerradas. Checkpoints durables:

1. manifest inicial;
2. `pending -> generating`;
3. `generating -> stored`;
4. error transient o permanent;
5. `uncertain`;
6. final `completed`.

`stored` nunca vuelve a pending/generating. `failed_permanent` y `uncertain` no reinician dentro
del mismo attempt. Un restart con entry `generating` y clip válido lo recupera sin provider; sin
clip válido cambia a `uncertain`. Un retry explícito crea un attempt nuevo y puede reutilizar clips
válidos de attempts anteriores por ID/ruta deterministas. El handler procesa secuencialmente y
los locks de runtime, manifest y asset evitan duplicación.

Si se cancela antes del provider no se genera. Durante ffmpeg se termina el proceso. Bytes todavía
no persistidos no se marcan `stored`; un clip ya persistido queda disponible para recovery. El
último checkpoint durable válido siempre se conserva.

## Reconciliación, lifecycle y configuración

La reconciliación reconoce `video-clip-generation-manifest.json` e inspecciona únicamente rutas
contractuales de jobs UUID. Reporta pares clip/sidecar incompletos, manifests inválidos, entry sin
artifact, artifact sin entry, clip corrupto, checksum/tamaño/MIME/codec/dimensiones/duración/fps,
audio inesperado y procedencia fuente distinta. Es estrictamente read-only: no borra, mueve ni
regenera video.

El shutdown detiene primero el worker y luego cierra video, imágenes, visual asset planning,
scene planning, scripting, planning y engine. Continúa cerrando recursos aunque uno falle.

Configuración global privada:

```text
ORION_VIDEO_CLIP_GENERATION_PROVIDER=simulated
ORION_VIDEO_CLIP_GENERATION_MODEL=simulated-video-v1
ORION_VIDEO_CLIP_GENERATION_OUTPUT_FORMAT=mp4
ORION_VIDEO_CLIP_GENERATION_CODEC=h264
ORION_VIDEO_CLIP_GENERATION_FRAME_RATE=24
ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS=4
ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS=10
ORION_VIDEO_CLIP_GENERATION_MAX_SOURCE_MANIFEST_BYTES=4000000
ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES=50000000
ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES=4000000
ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH=
ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH=
```

No se acepta configuración de video por job.

## Límites, pruebas y rollback

Las pruebas usan perfiles temporales, ffmpeg/ffprobe locales y cero red o coste. El rollback
operativo mantiene `simulated`; el rollback de código revierte la etapa, artifacts y módulos sin
migración porque `ArtifactType` se persiste como string.

El alcance original 5F.1 no incluía proveedor real. Fase 6F.1 completa el
adaptador OpenRouter Veo image-to-video como opt-in; sigue requiriendo que el
publication root configurado esté servido realmente por HTTPS.
# Extensión OpenRouter de Fase 6F.1

La infraestructura 5F.1 se conserva: el mismo handler/store/validator/manifest
admite metadata remota opcional y `simulated` continúa como default. El
adaptador OpenRouter añade capabilities, checkpoint pre-submit, cost gate,
remote job durable, polling reanudable, publicación del primer frame y descarga
segura. Está bloqueado por default y no se activa sin publisher HTTPS. Consulta
`PRODUCTION_OPENROUTER_VIDEO_PROVIDER.md`.

Los campos remotos son aditivos al schema 1.0.0. Provider, modelo, key,
publisher, base URL, duración, resolución, polling, coste máximo y autorización
facturable son globales privados y nunca se aceptan por job.

La siguiente fase recomendada es Fase 5F.3 — Secure Public Frame Publishing and
Controlled Live Validation. Todavía no existen publisher real, live
generation, audio, webhooks, timeline, render, DaVinci ni frontend.
