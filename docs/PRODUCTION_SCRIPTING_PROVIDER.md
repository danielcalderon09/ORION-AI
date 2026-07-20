# Proveedor intercambiable de SCRIPTING (Fase 5B.1)

SCRIPTING consume el `ProductionPlan` durable, valida su integridad y produce un
`ProductionScript` independiente del proveedor. `SCENE_PLANNING` y todas las etapas posteriores
continúan simuladas.

```text
StageCommand(SCRIPTING) -> StageContext -> DurableProductionPlanReader
  -> ProductionPlan -> ScriptingHandler -> ScriptingProvider
  -> ProductionScript -> LocalScriptingArtifactWriter
  -> production-script.json -> Artifact + StageResult
```

## Reader y contrato

El reader consulta metadata durable limitada al job y a `production_plan`, elige el input
explícito o el intento aplicable de forma determinista y exige la ruta contractual exacta. Antes
de devolver el modelo inmutable verifica confinamiento bajo `PROJECTS_DIR`, ausencia de rutas
absolutas/traversal/symlinks, archivo regular, límite de bytes, `size_bytes`, SHA-256, UTF-8,
JSON estricto sin claves duplicadas, `ProductionPlan` y schema soportado.

`ProductionScript` contiene versión, schema del plan origen, título, idioma, duración, tono,
hook, CTA opcional, escenas y metadata. Cada `ProductionScriptScene` conserva
`source_scene_number`, narración, duración, estilo de entrega, texto en pantalla, intención visual
y transición. Los modelos son frozen, `extra=forbid`, tienen 1..50 escenas consecutivas, una por
escena del plan, y rechazan secretos, paths, HTML ejecutable, comandos shell y metadata no JSON.

## Providers

`SimulatedScriptingProvider` es el default offline, determinista y rollback operativo; consume
el plan real. `OpenRouterScriptingProvider` es el proveedor real principal. Ambos implementan el
mismo puerto. OpenRouter comparte el transporte neutral OpenAI-compatible con Planning, usa
`/chat/completions`, Structured Outputs estricto, `require_parameters=true` y
`data_collection=deny`. No hay fallback a texto libre.

## Settings

- `ORION_SCRIPTING_PROVIDER=simulated` o `openrouter`.
- `ORION_SCRIPTING_MODEL=openai/gpt-4.1-mini` (configurable por entorno).
- `ORION_SCRIPTING_API_KEY` (solo con `openrouter`).
- `ORION_SCRIPTING_BASE_URL=https://openrouter.ai/api/v1`.
- `ORION_SCRIPTING_TIMEOUT_SECONDS=30`.
- `ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS=2` (1..5).
- `ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS=0.25`.
- `ORION_SCRIPTING_MAX_OUTPUT_TOKENS=8192`.
- `ORION_SCRIPTING_TEMPERATURE=0.2`.
- `ORION_SCRIPTING_MAX_PLAN_BYTES=1000000`.
- `ORION_SCRIPTING_MAX_SCRIPT_BYTES=2000000`.
- `ORION_OPENROUTER_HTTP_REFERER` y `ORION_OPENROUTER_APP_TITLE` (opcionales y globales).

La configuración pública por job admite solo tone, densidad, hooks/CTA, límites de palabras,
velocidad de lectura y preservación de texto. Provider, modelo, key, URL, headers, timeout,
retries, paths, clases, system prompt y schema externo producen 422.

## Artefacto, retry y recovery

El writer publica JSON canónico UTF-8 mediante temporal, fsync y replace atómico en
`production/<job_id>/scripting/attempt-<n>/production-script.json`. No sobrescribe contenido
incompatible y rechaza symlinks/escape. Registra tamaño/checksum reales, provider/modelo,
schemas, versión de prompt, artifact/checksum del plan origen, requested/reported model,
mismatch, métricas de tokens, latencia y número de escenas. Nunca registra keys o headers.

Timeout, conexión, 429 y 5xx usan retry interno acotado; agotarlo produce resultado transient y
el retry durable crea un attempt/ruta nuevos. Auth/configuración requiere intervención; JSON,
schema o scene mapping inválido es permanente. Recovery e idempotencia existentes evitan doble
ejecución, y el reconciliador común cubre solo rutas contractuales de plan/script.

## Guía manual segura

1. Instale mínimo con `pip install -e .` o LLM con `pip install -e ".[production-llm]"`.
2. Active `ORION_PROMPT_VIDEO_ENABLED=true`.
3. Seleccione Planning/Scripting (`simulated` por defecto, `openrouter` real).
4. Configure secretos solo por entorno y, opcionalmente, referer/título globales.
5. Inicie backend, cree un job y consulte eventos/artefactos existentes.
6. Verifique ambos JSON y sus SHA-256 bajo `PROJECTS_DIR`.
7. Vuelva ambos providers a `simulated` para rollback.

Las pruebas reales-falsas usan `httpx.MockTransport`; no hay red. Todavía no existen
SCENE_PLANNING real, imágenes, video, TTS, música, subtítulos, timeline/render, DaVinci ni
frontend de producción.
