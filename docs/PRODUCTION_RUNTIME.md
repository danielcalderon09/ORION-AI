# Runtime local de Production Pipeline

## Planning configurable (Fase 5A)

El registry recibe un único `PlanningHandler` dependiente de `PlanningProvider`; todos los
demás handlers permanecen simulados. El container selecciona `simulated` u `openai`,
inyecta el writer local y cierra uniformemente el provider antes de disponer el engine.
No se crea cliente real al importar ni con el feature principal apagado.

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
