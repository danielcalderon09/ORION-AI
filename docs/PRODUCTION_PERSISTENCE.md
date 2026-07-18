# Persistencia durable de Production

## 1. Alcance

La Fase 2 persiste una `OrchestrationDecision` de forma atómica usando SQLAlchemy 2, SQLite y
Alembic. No registra API, no inicia worker y no ejecuta proveedores o herramientas de medios.

## 2. Diagrama de tablas

```text
production_jobs (job_id PK, row_version)
  ├──< production_artifacts (artifact_id PK, job_id FK)
  ├──< stage_commands (command_id PK, job_id FK)
  │      ├──1 stage_results (command_id PK/FK)
  │      └──1 production_stage_runs.command_id
  ├──< production_stage_runs (stage_run_id PK, job_id FK)
  └──< production_events (event_id PK, job_id FK)
```

`StageResult` no contiene `result_id`. Se adopta la opción A: `command_id` es la identidad
persistente del resultado porque existe como máximo un resultado por comando.

## 3. Claves y restricciones

| Tabla | Identidad | Restricciones principales |
|---|---|---|
| `production_jobs` | `job_id` | `row_version >= 1`, timestamps ordenados |
| `stage_commands` | `command_id` | `idempotency_key` único, intento positivo |
| `stage_results` | `command_id` | un resultado por comando, progreso y timestamps válidos |
| `production_stage_runs` | `stage_run_id` | intento e idempotency únicos; `(job, stage, attempt)` único |
| `production_events` | `event_id` | `(job_id, sequence_number)` único |
| `production_artifacts` | `artifact_id` | `(job_id, relative_path)` único |

Todas las referencias de trabajo usan foreign keys con `PRAGMA foreign_keys=ON`. UUID y enums se
guardan como cadenas canónicas estables; snapshots, metadata y listas de UUID usan JSON, nunca
pickle.

## 4. Flujo de `persist_decision`

```text
abrir ProductionUnitOfWork
  → cargar y cotejar previous_job / row_version
  → añadir o actualizar updated_job
  → registrar artefactos provistos
  → cotejar/guardar processed_command y next_command
  → guardar processed_result y marcar comando procesado
  → validar referencias de artefactos
  → crear/actualizar production_stage_runs
  → validar e insertar eventos en secuencia
  → flush final
  → commit único
cualquier error → rollback y cierre
```

## 5. Ejemplo transaccional

Una decisión `QUEUED → RUNNING` actualiza el trabajo a `running`, inserta el primer
`StageCommand`, crea su `production_stage_run` y guarda `ProductionStageStarted`. Ninguno de esos
datos se hace visible por separado. Si el evento colisiona, también se revierten trabajo, comando y
stage run.

## 6. Optimistic locking

`ProductionJobRecord` configura `row_version` como `version_id_col` de SQLAlchemy. Inicia en 1 y
sube automáticamente en cada `UPDATE`. El repositorio mantiene el registro leído dentro de la
misma sesión; un flush cuya versión ya cambió produce `ProductionConcurrencyError`.

`OrchestrationDecisionStore` coteja además el trabajo durable con `previous_job`. Esto evita aplicar
una decisión calculada sobre un estado lógico diferente aunque la llamada use una sesión nueva.

## 7. Idempotencia durable

- Comandos: UUID y `idempotency_key` únicos.
- Resultados: `command_id` es primary key.
- Eventos: UUID y secuencia por trabajo únicos.
- Stage runs: UUID determinista derivado del comando y unicidad del intento.
- Artefactos: UUID inmutable y ruta única dentro del trabajo.
- Decisión repetida: si todos los registros reconstruyen exactamente los mismos contratos, devuelve
  éxito con `idempotent_replay=True` y no incrementa `row_version`.
- Mismo identificador con contenido distinto: `ProductionIdempotencyConflictError`.

## 8. Secuencia de eventos

La primera secuencia de un trabajo es 0. Cada evento nuevo debe continuar sin huecos desde el máximo
persistido. Los eventos repetidos solo se aceptan si ID, secuencia, envelope, payload y metadata
coinciden exactamente. `ProductionEventSequenceError` señala huecos o colisiones.

El audit log no es un `EventBus`; no existe publicación, suscripción, outbox ni entrega externa en
esta fase.

## 9. Mappers e integridad

Cada contrato tiene un mapper explícito. Los mappers convierten UUID/enums/columnas individualmente,
validan JSON portable y reconstruyen Pydantic para recuperar invariantes. Un tipo de evento
desconocido o registro inconsistente genera `ProductionRecordIntegrityError`. No abren sesiones,
hacen flush ni commit.

## 10. Sesión y rollback

Los repositorios reciben una `Session` creada por `ProductionUnitOfWork`. Hacen flush para detectar
constraints, pero nunca commit. La unidad de trabajo hace un único commit final; ante excepciones
de dominio, concurrencia, idempotencia o SQLAlchemy ejecuta rollback y siempre cierra la sesión.

## 11. SQLite y concurrencia

El engine se crea explícitamente y configura:

- `foreign_keys=ON`;
- `busy_timeout=5000`;
- timeout de conexión de cinco segundos;
- `check_same_thread=False` para permitir el futuro worker local.

SQLite permite múltiples lectores pero serializa escritores. No se añade pooling complejo. Los
métodos de repositorio conservan la interfaz async existente, aunque usan SQLAlchemy síncrono; la
Fase 3 deberá ejecutar ese trabajo fuera del event loop o decidir si incorpora un driver async.

## 12. Migraciones

Alembic usa `alembic.ini` y la carpeta histórica
`backend/src/infrastructure/persistence/sqlite/migrations`. La revisión `20260717_0001` crea solo
tablas `production`. El downgrade las elimina en orden seguro y no toca tablas históricas.

La URL debe proporcionarse explícitamente en pruebas. Si no se proporciona al ejecutar Alembic
manualmente, se obtiene de Settings. Ninguna migración se ejecuta desde imports o desde `main`.

## 13. Backup futuro

Antes de migrar una base real, la aplicación empaquetada deberá detener escritores, crear un backup
consistente mediante la API de backup de SQLite y registrar versión/checksum. Esa política se
diseñará en la Fase 12; esta fase no copia ni altera bases del usuario.

## 14. Pendiente para Fase 3

- API de creación/consulta/cancelación.
- Worker local, leases y heartbeat.
- `StageHandler` y ejecución de puertos simulados.
- Requeue de reintentos y recuperación al iniciar.
- Outbox/publicación de eventos.
- Política de backup y retención.
- Integración con proveedores, medios o DaVinci.
