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

## Endurecimiento de instalación (Fase 5A.1)

La instalación mínima es `pip install -e .` y conserva el provider `simulated` sin cargar
`httpx` ni el módulo del adaptador real. El soporte opcional se instala con
`pip install -e ".[planning-openai]"`; el extra declara directamente `httpx>=0.27,<1.0`.
El requirements histórico de desarrollo conserva el mismo rango porque sus tests usan
`MockTransport`.

Solo la selección efectiva de `openai`, con el feature principal activo y durante la
construcción del container, intenta importar el adaptador. Si falta el extra se lanza
`PlanningProviderDependencyError` con el mensaje accionable “OpenAI planning support is not
installed. Install the planning-openai extra.” No existe fallback silencioso. Con el feature
principal apagado no se construye container ni se valida dependencia o credencial.

`scripts/verify_planning_install_profiles.py` instala el árbol construido en dos venvs
temporales, ejecuta el perfil mínimo con `httpx` bloqueado y construye/cierra el adaptador con
cliente falso en el perfil opcional. No hace requests. CI ejecuta el mismo verificador y la
suite Planning sin red.

## Reproducibilidad del modelo

`ORION_PLANNING_MODEL` conserva el alias compatible `gpt-4.1-mini`. Para despliegues
reproducibles se recomienda configurar un identificador versionado cuando el catálogo del
proveedor lo permita. La API por job nunca acepta modelo. El artefacto conserva por separado
`requested_model` y `reported_model`; `model_version` usa el valor reportado cuando existe.
Una diferencia queda indicada con `model_mismatch`, sin registrar prompts ni respuestas.

## Reconciliación de artefactos huérfanos

El archivo se publica antes de que `OrchestrationDecisionStore` pueda confirmar su transacción.
No se añadió rollback al handler porque este no conoce persistencia y esa dependencia rompería
la frontera de ejecución. `LocalPlanningArtifactReconciler` resuelve el hueco antes de recovery:

1. consulta las rutas `production_plan` registradas con una sesión corta;
2. recorre sin seguir symlinks únicamente `production/<uuid>/planning/attempt-<n>`;
3. conserva archivos registrados y huérfanos recientes;
4. mueve huérfanos antiguos a cuarentena por defecto, o los elimina solo con configuración
   explícita;
5. limpia únicamente directorios vacíos bajo `production`.

Settings nuevos:

- `ORION_PLANNING_RECONCILE_ARTIFACTS=true`
- `ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS=300`
- `ORION_PLANNING_ORPHAN_ACTION=quarantine` (`delete` es opt-in)
- `ORION_PLANNING_QUARANTINE_DIR=production-quarantine` (POSIX relativo)

La operación es idempotente. Writer y reconciliador rechazan symlinks, traversal y rutas
absolutas mediante resolución del filesystem y relación real con `PROJECTS_DIR`. Un symlink,
fallo de consulta o error de cleanup produce `PlanningArtifactReconciliationError`; el
lifecycle cierra provider/engine y no inicia el worker. Logs y reportes contienen solo
contadores, nunca paths absolutos. No se toca ninguna otra etapa ni archivo fuera del workspace.
