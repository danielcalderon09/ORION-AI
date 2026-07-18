# Runtime local de Production Pipeline

## Alcance

La Fase 3 ejecuta el pipeline durable con handlers simulados. No registra rutas, no arranca el
worker desde FastAPI y no usa proveedores, multimedia ni editor. Su propósito es validar la
coordinación local antes de conectar capacidades reales.

## Flujo de un ciclo

```text
ProductionWorker.run_once
  -> recupera reintentos cuyo retry_at venció
  -> ProductionLeaseManager.acquire_next
  -> carga ProductionJob durable
  -> ProductionOrchestrator.decide
       queued  -> crea StageCommand
       running -> consume un StageResult
       cancel_requested -> cancela de forma segura
  -> OrchestrationDecisionStore.persist_decision (transacción única)
  -> libera lease
```

Un job `running` ejecuta exactamente un handler antes de decidir. `run_until_idle` repite ciclos
de forma explícita para pruebas y futura composición local; no cambia la semántica de un ciclo.

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

## Límites deliberados

- No hay API, WebSockets, frontend ni composición en el arranque.
- No hay procesos separados ni ejecución distribuida.
- SQLAlchemy sigue siendo síncrono; el runtime local deberá aislar el trabajo bloqueante cuando se
  integre con el ciclo de vida de la aplicación.
- No hay proveedores reales, editor, render real, archivos multimedia ni publicación de eventos.
- La creación de jobs sigue siendo responsabilidad de un futuro servicio de aplicación/API.
