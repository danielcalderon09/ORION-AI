# Provider de SCENE_PLANNING

## Arquitectura y pipeline

Fase 5C transforma un `ProductionScript` durable en un `ProductionScenePlan` durable. No usa el
prompt original y no genera imágenes, video ni assets. Fase 5D consume este artifact sin cambiar
el contrato de Scene Planning.

```text
StageCommand(SCENE_PLANNING)
  -> DurableProductionScriptReader
  -> ProductionScript verificado
  -> ScenePlanningHandler
  -> ScenePlanningProvider
  -> ProductionScenePlan validado
  -> ScenePlanningArtifactWriter
  -> scene-plan.json
  -> Artifact + StageResult
```

El dominio, orquestador y contrato del provider no conocen `httpx`, SQLAlchemy, headers, secretos
o rutas absolutas. El reader consulta `ArtifactRecord` mediante un repositorio read-only limitado
al job y a `ArtifactType.PRODUCTION_SCRIPT`.

## Contratos

`ProductionScenePlan` contiene identidad, idioma, duracion y escenas. Cada `ProductionScene`
preserva la narracion aprobada y tiene uno o mas `ProductionShot`. Camara, framing, movimiento,
timing y transicion son modelos/campos controlados; no se aceptan dicts de extension arbitrarios.

Las validaciones exigen:

- escenas y shots consecutivos;
- IDs contractuales y unicos;
- cada shot pertenece a su escena;
- timings positivos, contiguos y que cubren la escena;
- suma de escenas igual a la duracion del script;
- mapeo uno a uno y narracion identica al ProductionScript;
- transiciones tipadas y terminacion final `none`.

## Providers

`SimulatedScenePlanningProvider` es el default, no usa red y produce exactamente una toma
determinista por escena. `OpenRouterScenePlanningProvider` es la implementacion real principal;
`RealScenePlanningProvider` es su nombre contractual explicito. Ambos implementan
`ScenePlanningProvider.generate_scene_plan(script)` y reciben solo el `ProductionScript`.

OpenRouter reutiliza `OpenAICompatibleResponsesClient`, Structured Outputs estricto,
`require_parameters=true`, `data_collection=deny`, `store=false`, timeout y retries acotados. Las
pruebas usan exclusivamente `httpx.MockTransport`.

## Configuracion

```dotenv
ORION_SCENE_PLANNING_PROVIDER=simulated
ORION_SCENE_PLANNING_MODEL=openai/gpt-4.1-mini
ORION_SCENE_PLANNING_API_KEY=
ORION_SCENE_PLANNING_BASE_URL=https://openrouter.ai/api/v1
ORION_SCENE_PLANNING_TIMEOUT_SECONDS=30
ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS=2
ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS=0.25
ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS=8192
ORION_SCENE_PLANNING_TEMPERATURE=0.2
ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES=2000000
ORION_SCENE_PLANNING_MAX_PLAN_BYTES=4000000
```

Provider, modelo, key, URL, headers y retry son configuracion global; no se leen del job. Con
`simulated` o con Production apagado no se exige key ni se importa el provider real. Provider
desconocido, modelo vacio, key ausente o URL no HTTPS fallan sin fallback.

## Artifact e integridad

La ruta estable es:

```text
production/<job_id>/scene_planning/attempt-<n>/scene-plan.json
```

El writer serializa JSON canonico UTF-8, escribe un temporal, sincroniza y publica con
`os.replace()`. Rechaza traversal, escapes del workspace y symlinks. El `Artifact` registra MIME,
tamano y SHA-256 reales, provider/model, versiones, artifact/checksum del script origen, conteos de
escenas/shots, tokens opcionales y latencias. Nunca registra prompts, keys o headers.

## Recovery, idempotencia y rollback

Antes del provider, el writer busca un `scene-plan.json` del mismo job/intento. Solo se recupera si
UTF-8, JSON sin claves duplicadas, schema, duraciones y relacion con el script son validos; en ese
caso el provider no se ejecuta. Un archivo incompatible produce error permanente y no se
sobrescribe. El runtime conserva leases, command idempotency y retry durable con una ruta nueva por
attempt. Los artifacts huerfanos antiguos entran en la reconciliacion conservadora.

Rollback operativo:

```dotenv
ORION_SCENE_PLANNING_PROVIDER=simulated
```

## Limites deliberados

Fase 5C no implementa assets, Image Provider, Video Provider, Voice Provider, narracion real,
musica, subtitulos, timeline, render, DaVinci, Electron, frontend, WebSockets, Redis, OpenCV ni
FFmpeg.

La etapa siguiente implementada es VISUAL_ASSET_PLANNING, que genera únicamente especificaciones
JSON. La generación o adquisición real de assets visuales permanece pendiente.
