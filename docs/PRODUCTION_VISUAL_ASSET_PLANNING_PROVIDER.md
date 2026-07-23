# Provider de VISUAL_ASSET_PLANNING

## Propósito y flujo

Fase 5D convierte el `ProductionScenePlan` durable aprobado en especificaciones visuales
ejecutables por una fase futura. No usa el prompt original, ProductionPlan ni ProductionScript y
no genera, descarga ni almacena imágenes o videos.

```text
StageCommand(VISUAL_ASSET_PLANNING)
  -> DurableProductionScenePlanReader
  -> ProductionScenePlan verificado
  -> VisualAssetPlanningHandler
  -> VisualAssetPlanningProvider
  -> ProductionVisualAssetPlan validado
  -> VisualAssetPlanningArtifactWriter
  -> visual-asset-plan.json
  -> Artifact + StageResult
```

## Contratos y mapping

`ProductionVisualAssetPlan` incluye schema/source version, título, idioma, aspect ratio, dirección
global, negative prompt opcional, `VisualConsistencyProfile`, assets y metadata segura.
`ProductionVisualAssetSpec` referencia exactamente una escena y un shot, conserva
`ProductionCamera` y timing y declara kind, mode, prompt, composición, luz, color, estilo,
continuidad, dimensiones, referencias, duración y seed policy.

Las referencias solo pueden apuntar a assets anteriores; por tanto no existe autorreferencia ni
ciclo. Los IDs son únicos y el orden es scene/shot/asset. Cada shot tiene al menos un asset
principal. Width/height se limitan a 64–8192 y deben coincidir con 16:9, 9:16 o 1:1 con tolerancia
relativa de 1 %. La biblia visual usa IDs internos `character_NN`, `location_NN` y `prop_NN`; no
almacena imágenes, embeddings, biometría ni datos personales sensibles.

## Reader e integridad

El reader limita la consulta al job y a `PRODUCTION_SCENE_PLAN`, prioriza `input_artifact_ids` y
usa attempt/created_at/artifact ID para el fallback determinista. Rechaza ambigüedad, tipo
incorrecto, ruta no contractual, traversal, symlinks, archivos no regulares, exceso de tamaño,
size/SHA mismatch, UTF-8 o JSON inválido, constantes no estándar, claves duplicadas, schema
inválido y versión no soportada. La metadata de origen se reduce a una allowlist.

## Configuración pública

La sección por job es `configuration.visual_asset_planning` y admite solamente:

- `preferred_asset_kind`, `images_per_shot`, `allow_video_specs`;
- `allow_reference_assets`, `continuity_strength`, `prompt_detail_level`;
- `negative_prompt_enabled`, `target_width`, `target_height`, `safe_content_only`.

Provider, model, key, URL, headers, timeout, retries, paths, clases, prompt de sistema y schemas
externos son privados y producen HTTP 422 si se intentan enviar por job.

## Providers

`SimulatedVisualAssetPlanningProvider` es el default offline, determinista y reproducible.
Conserva cada scene/shot/camera/timing, crea al menos un asset principal por shot, IDs estables y
referencias de continuidad hacia atrás.

`OpenRouterVisualAssetPlanningProvider` reutiliza `OpenAICompatibleResponsesClient`. Envía
Structured Outputs estricto al endpoint `/chat/completions`, con `require_parameters=true`,
`data_collection=deny`, `store=false` y `stream=false`. No hay fallback a texto libre, reparación
regex, catálogo de modelos ni selección automática. Requested y reported model se registran por
separado; model, usage, request ID y finish reason reportados son opcionales. Un modelo sin
Structured Outputs produce un error tipado permanente.

Todas las pruebas HTTP usan `httpx.MockTransport`; no se hace red real.

## Settings globales

```dotenv
ORION_VISUAL_ASSET_PLANNING_PROVIDER=simulated
ORION_VISUAL_ASSET_PLANNING_MODEL=openai/gpt-4.1-mini
ORION_VISUAL_ASSET_PLANNING_API_KEY=
ORION_VISUAL_ASSET_PLANNING_BASE_URL=https://openrouter.ai/api/v1
ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS=30
ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS=2
ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS=0.25
ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS=12000
ORION_VISUAL_ASSET_PLANNING_TEMPERATURE=0.2
ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES=4000000
ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES=8000000
ORION_OPENROUTER_HTTP_REFERER=
ORION_OPENROUTER_APP_TITLE=
```

Con `simulated` o Production apagado no se exige key ni se importa el adapter real. `openrouter`
exige modelo no vacío, key y URL HTTPS sin credenciales. HTTP-Referer y X-Title son opcionales,
globales y nunca se aceptan por job ni se guardan en metadata.

## Artifact, recovery y retries

Ruta contractual:

```text
production/<job_id>/visual_asset_planning/attempt-<n>/visual-asset-plan.json
```

El writer usa JSON canónico UTF-8, temporal en el mismo directorio, flush, fsync y `os.replace`;
calcula tamaño y SHA reales y nunca sobrescribe contenido incompatible. Metadata pública incluye
versiones, source artifact/checksum, prompt version, conteos, provider/model, mismatch, tokens y
latencias, pero nunca prompts, respuestas crudas, keys, headers o paths absolutos.

Recovery valida el archivo del mismo attempt y su relación exacta con el Scene Plan antes del
provider. Corrupción o source diferente produce error explícito. Timeout, conexión, rate limit y
5xx agotados son transient y pasan al retry durable; contrato, auth, configuración y Structured
Outputs son permanent. El siguiente attempt usa otra ruta. Reconciliación solo examina los cuatro
nombres JSON contractuales y pone huérfanos antiguos en cuarentena por defecto.

Rollback:

```dotenv
ORION_VISUAL_ASSET_PLANNING_PROVIDER=simulated
```

## Límites deliberados

Todavía no existen generación real de imágenes o video, descarga/adquisición de assets,
almacenamiento binario, thumbnails, embeddings, visión/facial, TTS, música, SFX, subtítulos,
timeline, render, DaVinci ni frontend de producción. La fase recomendada siguiente es
generación/adquisición real de assets visuales bajo puertos separados.
