# Proveedor intercambiable de planificación (Fase 5A)

## Alcance

Solo `ProductionStage.PLANNING` puede usar un proveedor real. Las etapas de guion,
escenas, assets, narración, música, subtítulos, timeline, render, validación y handoff
continúan simuladas. El dominio y `ProductionOrchestrator` no conocen proveedores.

```text
StageCommand + StageContext
  -> PlanningHandler
  -> PlanningProvider
  -> ProductionPlan validado
  -> PlanningArtifactWriter
  -> production-plan.json + Artifact
  -> StageResult
  -> persistencia durable existente
```

## Contratos

`production.planning.models.ProductionPlan` es el resultado ejecutable de PLANNING y no
reemplaza el contrato histórico de Fase 1. Contiene versión, título, resumen, idioma,
duración, aspect ratio, estilos, metadata y entre 1 y 50 `ProductionScenePlan`. Las escenas
son consecutivas desde 1 y sus duraciones suman la duración objetivo.

Los modelos son Pydantic v2, inmutables, `extra=forbid` y JSON-serializables. Rechazan
paths absolutos/traversal, claves sensibles, HTML ejecutable y comandos de shell.

`PlanningProviderRequest` contiene exclusivamente IDs, prompt, configuración pública,
duración, idioma, aspect ratio, correlación e intento. `PlanningProviderResponse` traduce
el resultado externo a contratos internos; nunca expone clientes, headers, credenciales o
respuesta HTTP cruda.

## Providers

`ORION_PLANNING_PROVIDER=simulated` es el default. `SimulatedPlanningProvider` es
determinista, no usa red ni credenciales y permite rollback inmediato.

`ORION_PLANNING_PROVIDER=openai` selecciona el único adaptador real. Usa `httpx.AsyncClient`
contra OpenAI Responses API, solicita Structured Outputs mediante JSON Schema estricto,
envía `store=false`, aplica timeout y cierra el cliente en shutdown. No hay cliente en
import time.

`PlanningPromptBuilder` versión `1.0.0` genera instrucciones y payload de forma pura. El
adaptador no incrusta el prompt. No se intenta reparar JSON mediante regex: JSON inválido o
contrato inválido se rechaza.

## Configuración y secretos

Settings globales:

- `ORION_PLANNING_PROVIDER=simulated`
- `ORION_PLANNING_MODEL=gpt-4.1-mini`
- `ORION_PLANNING_API_KEY` (solo obligatorio con `openai`)
- `ORION_PLANNING_BASE_URL=https://api.openai.com/v1`
- `ORION_PLANNING_TIMEOUT_SECONDS=30`
- `ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS=2` (máximo 5)
- `ORION_PLANNING_RETRY_BASE_DELAY_SECONDS=0.25`
- `ORION_PLANNING_MAX_OUTPUT_TOKENS=4096`
- `ORION_PLANNING_TEMPERATURE=0.2`

La API key es `SecretStr`, solo se inyecta al constructor y nunca entra en DB,
`StageContext`, eventos, artefactos o respuestas. Provider desconocido o credencial ausente
falla durante startup con mensaje seguro; no hay fallback silencioso.

La configuración pública por job admite únicamente `language`,
`target_duration_seconds`, `aspect_ratio`, `visual_style`, `narrative_style` y
`scene_count_hint`. No admite provider, modelo, URL, headers, credenciales, timeout o path.

## Timeouts y retries

El retry interno cubre únicamente timeout, conexión, rate limit y disponibilidad antes de
obtener una respuesta confiable. Tiene máximo acotado y backoff exponencial sin jitter por
defecto. `CancelledError` siempre se propaga. Una respuesta recibida pero inválida no se
regenera silenciosamente.

Si los intentos de transporte se agotan, `PlanningHandler` produce
`FAILED_TRANSIENT`; `ProductionOrchestrator` y recovery controlan el retry durable. Errores
de configuración/autenticación producen `NEEDS_USER_ACTION`, y respuestas inválidas
producen `FAILED_PERMANENT`.

## Artifact writer

`LocalPlanningArtifactWriter` recibe `PROJECTS_DIR`, resuelve el destino bajo esa raíz,
escribe UTF-8 a un temporal, ejecuta `os.replace` atómico y limpia temporales ante error.
El contrato solo conserva la ruta POSIX relativa:

```text
production/<job_id>/planning/attempt-<n>/production-plan.json
```

El JSON es canónico. `size_bytes` y SHA-256 se calculan sobre los bytes exactos escritos.
El registro SQL del `Artifact` continúa dentro de `OrchestrationDecisionStore`.

## Observabilidad

Se registran IDs, etapa, intento, provider, modelo, latencia, escenas, tamaño, longitud y
hash corto del prompt. Nunca se registra prompt/respuesta completos, narración, API key,
Authorization o URLs con credenciales. Metadata pública puede incluir versión de schema y
prompt, token counts, latencia y request ID seguro.

## Guía manual segura

1. Verificar que la base tenga la migración ya requerida por Fase 4; Fase 5A no añade DB.
2. Configurar `ORION_PROMPT_VIDEO_ENABLED=true`.
3. Elegir `ORION_PLANNING_PROVIDER=simulated` o `openai`.
4. Para `openai`, definir `ORION_PLANNING_API_KEY` solo mediante entorno.
5. Iniciar backend y crear un job mediante la API existente.
6. Consultar eventos y artefactos; el contenido del plan no se devuelve en listados.
7. Para rollback, volver a `ORION_PLANNING_PROVIDER=simulated` y reiniciar.

Las pruebas automatizadas usan transports falsos, SQLite/workspaces temporales y ninguna
llamada facturable.
