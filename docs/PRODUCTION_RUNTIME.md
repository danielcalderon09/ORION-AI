# Runtime local de Production Pipeline

## RENDERING_LONG_FORM preparation-only (Fase 5H.3)

Despues de BUILDING_TIMELINE y antes de VALIDATING_RENDER, el runtime inyecta
`LocalRenderPreparationHandler`. Verifica los dos artifacts de composicion
durable, deriva un request estable, checkpointa un manifest
`prepared -> validating -> validated` e invoca exclusivamente
`DryRunRenderer`. Retry reutiliza el request; una validacion interrumpida se
repite sin riesgo de doble render porque no existe render real.

El reconciliador reporta estado sin escribir ni invocar renderer. Un output
futuro inesperado se conserva y requiere intervencion. La etapa emite solo dos
JSON y nunca `LONG_FORM_RENDER`; no crea MP4, directorio de output, proceso,
comando o conexion. Detalles en `PRODUCTION_LOCAL_RENDER_CONTRACT.md`.

## GENERATING_VIDEO_CLIPS durable (Fase 5F.1)

Después de ACQUIRING_ASSETS y antes de audio, `VideoClipGenerationHandler` consume únicamente el
manifest de imágenes completed y sus SOURCE_IMAGE verificados. Procesa secuencialmente, marca
`pending -> generating` antes de ffmpeg y `generating -> stored` solo después de store y ffprobe.
Publica MP4 H.264 sin audio y un manifest por attempt.

La composición no ejecuta ffmpeg/ffprobe durante startup. El provider simulado offline se cierra
antes del provider de imágenes. Recovery reutiliza clips válidos sin recodificar; un
`generating` ambiguo sin clip válido pasa a `uncertain`. El reconciliador de video es read-only.
Detalles en `PRODUCTION_VIDEO_CLIP_GENERATION.md`.

## ACQUIRING_ASSETS durable (Fase 5E.2)

Después de VISUAL_ASSET_PLANNING, `ImageAcquisitionHandler` lee el plan visual durable, admite
solo still image/text-to-image y procesa secuencialmente cada especificación. El manifest se
checkpointa antes de invocar al provider y después de cada `BinaryAssetWriter.write`. El provider
simulado es el default; OpenRouter usa de forma lazy `POST /api/v1/images`.

Recovery verifica manifest, source artifact/SHA, sidecar, bytes, MIME, tamaño, dimensiones y
mapping antes de reutilizar. `generating` sin binario verificable pasa a `uncertain` y requiere
retry explícito para evitar coste duplicado. Shutdown cierra Image Acquisition, Visual Asset
Planning, Scene Planning, Scripting, Planning y finalmente el engine.

## Almacenamiento binario durable

Production compone un `FilesystemBinaryAssetStore` provider-neutral que implementa los puertos de
lectura y escritura. Cada imagen se publica mediante temporal, `fsync` y replace atómico junto a
metadata canónica. Cada lectura vuelve a validar archivo regular, MIME, extensión, dimensiones,
tamaño y SHA-256 bajo `WorkspaceConfinement`; links, junctions, hard links, traversal y escapes se
rechazan.

El reconciliador común conserva su política cerrada para los cuatro JSON creativos y añade un
diagnóstico separado de pares binario/sidecar únicamente en
`production/<uuid>/assets/images`. Recovery reutiliza una pareja existente solo cuando bytes,
integridad y metadata coinciden exactamente. Esta infraestructura no añade una etapa de
generación ni inicia recursos async, clientes HTTP o tasks.

## VISUAL_ASSET_PLANNING durable (Fase 5D)

La etapa se ejecuta inmediatamente después de SCENE_PLANNING. El
`DurableProductionScenePlanReader` selecciona únicamente un
`ArtifactType.PRODUCTION_SCENE_PLAN` del mismo job, prioriza los inputs explícitos y verifica ruta
contractual, confinamiento, symlinks, archivo regular, límite de bytes, tamaño, SHA-256, UTF-8,
JSON estricto sin duplicados y schema. El handler recibe el contrato validado; nunca recibe el
prompt original, ProductionPlan ni ProductionScript.

El provider produce solo `ProductionVisualAssetPlan`: especificaciones visuales, referencias
deterministas y continuidad; el writer publica `visual-asset-plan.json` mediante temporal,
`fsync` y `os.replace`. Recovery reutiliza únicamente el archivo del mismo attempt cuando su
artifact/checksum de origen y mapping completo todavía coinciden. Retry durable usa un nuevo
attempt. La reconciliación conservadora reconoce exclusivamente plan, script, scene plan y visual
asset plan.

`simulated` es el provider default y offline. `openrouter` reutiliza el cliente neutral
OpenAI-compatible, es lazy y no hace llamadas en startup. Shutdown cierra Visual Asset Planning,
Scene Planning, Scripting, Planning y finalmente el engine.

## SCENE_PLANNING durable (Fase 5C)

Production inyecta un `ScenePlanningHandler` independiente despues de SCRIPTING. El handler lee
`production-script.json` mediante metadata durable del job, verifica path, symlinks, tamano,
SHA-256, UTF-8, JSON sin claves duplicadas y `ProductionScript`, y solo entonces invoca el
provider seleccionado. El resultado validado se publica como `scene-plan.json` canonico y
`ArtifactType.PRODUCTION_SCENE_PLAN`.

Antes de invocar al provider, el writer inspecciona la ruta contractual del mismo intento. Si ya
existe un scene plan valido y coherente con el script aprobado, recovery reconstruye el resultado
sin otra generacion. El runtime durable sigue garantizando una sola persistencia por comando y
leases exclusivos. Reconciliacion incluye plan, script y scene plan, sin seguir symlinks.

El provider default es `simulated`; `openrouter` usa el transporte neutral lazy. Assets reales,
narracion, musica, subtitulos,
timeline, render y handoff permanecen simulados.

## SCRIPTING durable (Fase 5B)

El registry de Production reemplaza PLANNING y SCRIPTING. SCRIPTING selecciona el plan durable,
valida path, symlinks,
tamano, SHA-256, UTF-8, JSON y contrato, invoca un provider y publica
`production-script.json` canonico.

Retry genera attempt/ruta nuevos; recovery puede seleccionar el plan registrado mas reciente
si el command no conserva inputs. Reconciliacion cubre rutas contractuales de plan/script.
Los clientes OpenRouter sobre transporte OpenAI-compatible son lazy. Shutdown cierra
Scripting, Planning y engine tras el worker; el feature apagado no construye clientes.

## Planning configurable (Fase 5A)

El registry recibe un único `PlanningHandler` dependiente de `PlanningProvider`; todos los
demás handlers permanecen simulados. El container selecciona `simulated` u `openrouter`,
inyecta el writer local y cierra uniformemente el provider antes de disponer el engine.
No se crea cliente real al importar ni con el feature principal apagado.

Fase 5B.1 formaliza `httpx` en el extra neutral `production-llm` (con aliases históricos
`planning-openai`/`production-openai`) y mantiene reconciliación conservadora
del JSON de planificación. El orden de startup con el feature activo es: construir container,
validar esquema, reconciliar artifacts, ejecutar recovery e iniciar el worker. Un fallo de
integridad durante reconciliación cierra recursos parciales y evita crear la task. La consulta
SQL y el recorrido de filesystem se ejecutan en un thread con sesión propia; no se comparte una
`Session` con el event loop.

La reconciliación solo inspecciona rutas contractuales de PLANNING, no sigue symlinks y mantiene
una edad mínima para no competir con intentos activos. Cuarentena es la acción predeterminada.
El feature principal apagado no construye el reconciliador ni toca DB/workspace.

## Composición HTTP y lifecycle (Fase 4)

`production/composition/container.py` construye el runtime sin service locator global.
`ProductionWorker` se inicia solo dentro del lifespan con el feature flag activo. Startup
valida revisión, ejecuta recovery y crea una única task; shutdown señaliza, espera hasta el
timeout, cancela si es necesario y dispone el engine. La creación HTTP deja el job en
`QUEUED`; el worker sigue siendo el único generador y ejecutor de comandos.

## Alcance

La Fase 3 ejecuta el pipeline durable con handlers simulados. No registra rutas, no arranca el
worker desde FastAPI y no usa proveedores, multimedia ni editor. Su propósito es validar la
coordinación local antes de conectar capacidades reales.

## Auditoría previa a Fase 3.5

La implementación de Fase 3 era funcional, pero `ProductionWorker` acumulaba claim, cuatro
lecturas SQL, selección por estado, heartbeat, ejecución y persistencia. `ProductionRecoveryService`
repetía consultas de job/eventos y `ProductionLeaseManager` mezclaba política de ownership con
upsert/update/delete SQL. La lectura de comando usaba `LIMIT 1`, por lo que dos comandos pendientes
se habrían ocultado.

Sin contar las consultas internas de `OrchestrationDecisionStore`, un ciclo sin retries hacía una
consulta de candidatos de recovery; el claim hacía selección, upsert y recarga; y el procesamiento
añadía entre dos y tres lecturas según estado. Con `N` retries, recovery añadía dos consultas por
candidato. Fase 3.5 encapsula y hace comprobables esas lecturas; no promete todavía optimización o
batching.

## Flujo de un ciclo refactorizado

```text
ProductionWorker.run_once
  -> ProductionRecoveryService.requeue_due_retries
  -> ProductionLeaseManager -> LeaseRepository.acquire_next
  -> ClaimedJobProcessor.process
       -> RuntimeStateReader carga contratos durables
       -> ProductionOrchestrator.decide
       -> StageContextFactory crea StageContext
       -> ProductionExecutor ejecuta un StageHandler
       -> RuntimeDecisionPersister -> OrchestrationDecisionStore (transacción única)
  -> ProductionLeaseManager -> LeaseRepository.release (finally)
```

`ProductionWorker` es una fachada: recupera, solicita claim, delega y libera. `ProductionWorkerLoop`
contiene `run_until_idle` y `run_forever`. `ClaimedJobProcessor` procesa exactamente una decisión
durable y maneja `queued`, `running` y `cancel_requested` sin adquirir ni liberar leases.

## Lecturas y sesiones

`RuntimeStateReader` es el único lector de jobs, comandos, secuencias, intentos y retries. Abre y
cierra una sesión por operación, nunca hace commit y devuelve únicamente contratos validados. Dos
comandos pendientes producen `MultiplePendingStageCommandsError`.

`SQLAlchemyLeaseRepository` posee las sesiones/transacciones cortas y el SQL de lease.
`ProductionLeaseManager` solo valida owner, reloj, duración y ownership. No importa SQLAlchemy ni
records. El esquema `production_leases` no cambia y no se añade migración.

## StageContext y executor

`StageContext` es inmutable, versionado y serializable. Contiene identidades, configuración,
artefactos de entrada, correlación y un workspace POSIX relativo estable:
`production/<job>/<stage>/attempt-<n>`. Rechaza paths absolutos/traversal y claves con apariencia de
credencial. No contiene sesiones ni servicios.

`StageContextFactory` valida coincidencia job/comando y no realiza IO. `ProductionExecutor` valida
contexto, ejecuta exactamente un handler, comprueba resultado/artefactos/paths y detecta mutación
del comando o contexto.

## Lease y heartbeat

`production_leases.job_id` es a la vez clave primaria y foreign key. Por ello solo puede existir
una lease por trabajo. El claim SQLite usa un upsert condicionado a que la lease haya vencido o ya
pertenezca al mismo owner. `ProductionHeartbeat` renueva vencimiento y versión mientras una etapa
está activa. La liberación exige el mismo owner.

Una lease expirada no altera el estado del job: otro worker puede reclamar un job `running`, cargar
su único comando no procesado y continuar. Esto evita inventar transiciones de dominio para una
condición puramente operativa.

## Dispatcher, executor y handlers

`StageHandlerRegistry` mantiene el mapa `ProductionStage -> StageHandler` y rechaza registros
duplicados. `ProductionExecutor` ejecuta una sola vez el handler resuelto y valida identidad de
comando, job, etapa y artefactos. Nunca persiste.

Los handlers simulados cubren planificación, guion, escenas, recursos, narración, música,
subtítulos, timeline, render, validación y handoff opcional a clips. Devuelven un artefacto de
metadatos relativo y marcado `simulated`; no escriben el archivo que describen.

## Reintentos y cancelación

`failed_transient` deja el job en `waiting_for_retry` y el orquestador registra
`ProductionRetryScheduled`. La recuperación lee ese evento durable y solo crea la transición a
`queued` al llegar `retry_at`. El siguiente claim genera un nuevo intento con número incremental.

Si el worker reclama un job ya marcado `cancel_requested`, no crea un comando nuevo. Cuando existe
un comando de la etapa actual todavía no procesado, termina esa etapa y persiste resultado,
artefactos y cancelación en una única decisión. Si la solicitud aparece durante la ejecución, el
worker vuelve a cargar el job antes de decidir y aplica la misma regla.

Recovery es independiente del worker: `recover`, `requeue_due_retries` e
`inspect_expired_leases` no adquieren leases ni ejecutan handlers. Una carrera de requeue ganada por
otro proceso se reconoce por optimistic locking; errores reales de integridad se propagan.

## Política de SQLAlchemy síncrono

No se migra a SQLAlchemy async. Claim, heartbeat y release son transacciones breves y síncronas.
Las lecturas pueden aislarse con `ThreadedRuntimeBlockingExecutor`, que usa `asyncio.to_thread` y
ejecuta una operación que crea/cierra su propia sesión. La persistencia atómica puede aislarse con
`ThreadedRuntimeDecisionPersister` bajo la misma regla. Las pruebas usan implementaciones
inmediatas y deterministas. Nunca se comparte una `Session` entre threads y no existe un
`ThreadPoolExecutor` global propio.

## Límites deliberados

- No hay API, WebSockets, frontend ni composición en el arranque.
- No hay procesos separados ni ejecución distribuida.
- La composición con el ciclo de vida del backend sigue pendiente; los adaptadores threaded están
  disponibles pero el worker no se inicia automáticamente.
- No hay proveedores reales, editor, render real, archivos multimedia ni publicación de eventos.
- La creación de jobs sigue siendo responsabilidad de un futuro servicio de aplicación/API.
# Extensión runtime de Fase 5F.2

OpenRouter video es una selección global opcional. No se importa ni construye
en modo simulated. En modo OpenRouter, composición exige autorización
facturable, `SecretStr` y publisher real; como Fase 5F.2 solo entrega publisher
disabled, falla cerrado sin abrir red. Los tests remotos usan MockTransport y
publisher in-memory. Remote jobs se checkpointan antes del primer poll y
recovery nunca repite un POST facturable.
