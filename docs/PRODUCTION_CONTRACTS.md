# Contratos del bounded context `production`

## Contrato ejecutable de planificación (Fase 5A)

`production.planning.ProductionPlan` y `ProductionScenePlan` son contratos estrictos para
la salida de PLANNING. Se distinguen del plan histórico de Fase 1 por namespace. El puerto
`PlanningProvider` intercambia únicamente `PlanningProviderRequest/Response`; el writer
intercambia contenido canónico por ruta relativa, checksum y tamaño, sin persistir estados.

## Contratos públicos de Fase 4

Los contratos HTTP son traducciones explícitas, no records ORM. `client_request_id` y el
fingerprint representan una petición durable y están separados de la idempotencia por
etapa. La respuesta expone configuración sanitizada, progreso, versión optimista y
timestamps, pero nunca el fingerprint. Query ports read-only devuelven modelos de
aplicación mediante sesiones cortas.

Este documento describe los contratos introducidos en la Fase 1. Son estructuras de datos y puertos; todavía no están conectados a FastAPI, workers, proveedores ni editores.

La Fase 1.5 incorpora contratos de ejecución y un coordinador de decisiones puro. Tampoco publica
eventos ni ejecuta capacidades externas.

## Contratos ejecutables de SCRIPTING (Fase 5B)

`production.scripting.ProductionScript` es independiente de `ProductionPlan`. Sus
`ProductionScriptScene` consecutivas referencian exactamente una escena origen, en el mismo
orden, idioma y duracion. `ReadProductionPlan`, `ScriptingConfiguration` y los contratos del
provider no contienen ORM, cliente HTTP, headers, keys ni paths absolutos.

`ArtifactType.PRODUCTION_SCRIPT` es aditivo. La columna SQL almacena strings, no un enum
nativo, por lo que Fase 5B no requiere migracion.

## Contratos ejecutables de SCENE_PLANNING (Fase 5C)

`production.scene_planning.ProductionScenePlan` consume exclusivamente un
`production.scripting.ProductionScript` aprobado. Sus `ProductionScene` preservan narracion,
duracion, orden y mapeo de origen. Cada escena contiene `ProductionShot` consecutivos con
`ProductionCamera`, `ProductionTiming` contiguo y `ProductionTransition` controlada. IDs de
escena/shot son contractuales y unicos; la ultima toma usa `none` y las escenas anteriores deben
transicionar.

Los modelos son estrictos, inmutables y no contienen dicts arbitrarios. El contrato se valida de
nuevo contra el script despues del provider y durante recovery. `ArtifactType.PRODUCTION_SCENE_PLAN`
es aditivo sobre la columna textual existente, por lo que Fase 5C no requiere migracion.

## Contratos ejecutables de VISUAL_ASSET_PLANNING (Fase 5D)

`ProductionVisualAssetPlan` es independiente de los artifacts multimedia. Consume solo un
`ProductionScenePlan` aprobado y conserva título, idioma, scene/shot IDs, cámara y duración.
Contiene `ProductionVisualAssetSpec`, `VisualComposition` y `VisualConsistencyProfile` tipados;
`AssetKind`, `GenerationMode`, `SeedPolicy`, roles y clases de entidad son enums cerrados.

`validate_visual_asset_plan_against_scene_plan` exige al menos un asset principal por shot,
mapping exacto, IDs únicos, orden determinista, dimensiones/ratio coherentes y referencias
anteriores, válidas y acíclicas. La procedencia durable incluye artifact ID y SHA-256 del scene
plan. `ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN` se guarda como string en la columna existente,
por lo que Fase 5D no requiere migración.

## Contratos durables de assets binarios

`ProductionBinaryAsset`, `ProductionBinaryAssetMetadata` y
`ProductionBinaryAssetReference` separan bytes, procedencia segura y expectativa de lectura.
Conservan job/scene/shot, rol, MIME, extensión, checksum, tamaño, dimensiones, timestamp UTC y
ruta POSIX contractual. Son modelos frozen con campos extra prohibidos; la metadata rechaza
secretos y rutas absolutas, pero permite métricas no secretas como `input_tokens`.

`BinaryAssetStore` combina los puertos `BinaryAssetWriter` y `BinaryAssetReader`.
`BinaryAssetIntegrityValidator` compone validadores de MIME, SHA-256 y tamaño sin conocer
filesystem, ORM, API o proveedores. El adaptador local conserva metadata en un sidecar JSON
estricto y el archivo en `production/<job_id>/assets/images`. `ArtifactType.SOURCE_IMAGE` ya
existe y se persiste como string, así que no se necesita migración.

## Principios

- El dominio es importable sin FastAPI, OpenCV, FFmpeg, DaVinci o acceso a IO.
- Los modelos Pydantic rechazan campos desconocidos, son inmutables y se serializan a JSON.
- UUID, enums y versiones forman parte del contrato; no son detalles de una implementación.
- Los módulos se comunican con rutas relativas seguras y referencias de artefacto, no con rutas privadas absolutas.
- Un puerto describe una capacidad. La aplicación depende del puerto y la infraestructura implementa el adaptador.

## Modelos

### `ProductionJob`

Representa el agregado durable de una producción iniciada desde un prompt. Lo creará en el futuro el servicio de aplicación al aceptar una solicitud; lo consumirán el repositorio, el worker y los casos de uso de cada etapa.

Mantiene identidad, prompt, `status`, `current_stage`, timestamps UTC, snapshot de configuración, error opcional y referencias al render largo y al proyecto de clips. No almacena el archivo de video ni objetos de infraestructura.

### `ProductionPlan`

Representa el plan creativo y técnico aprobado para el trabajo. Lo producirá `PlannerPort`; lo consumirán guion, planificación de escenas, proveedores y ensamblaje del paquete de edición.

La versión es explícita. Anchura, altura, FPS y duración deben ser positivos; el orden y los IDs de escenas son únicos; la suma de duraciones de escenas coincide con la duración total con tolerancia de 0,01 segundos.

### `ScenePlan`

Representa una unidad narrativa todavía independiente de un archivo concreto. La producirá `ScenePlannerPort`; la consumirán adquisición de recursos, narración/subtítulos y el ensamblador de edición.

Incluye ID estable, orden, duración, narración, descripción/consulta visual, tipo de recurso, movimiento, transición y texto en pantalla opcional. Tipo, movimiento y transición usan enums controlados.

### `Artifact`

Representa el registro de un archivo producido o adquirido. Lo producirán adaptadores y el futuro `ArtifactStorePort`; lo consumirán etapas posteriores mediante UUID.

Incluye tipo, ruta relativa, MIME, estado, tamaño, SHA-256, duración/dimensiones y procedencia opcionales. `metadata` permite datos auxiliares versionados sin convertirlos en campos de control.

### `EditPackage`

Representa una timeline completamente resuelta y agnóstica del editor. La producirá el ensamblador de producción; la consumirá `EditorPort`.

Incluye lienzo, FPS, duración, escenas concretas, inventario de artefactos, referencias opcionales de narración/música/subtítulos, preset y salida relativa. Valida que:

- escenas y artefactos tengan IDs únicos;
- las órdenes no se repitan;
- todos los artefactos pertenezcan al trabajo;
- cada escena referencie una imagen/video declarado y del tipo correcto;
- narración, música y subtítulos referencien tipos correctos;
- la duración total sea coherente.

## Puertos y dirección de dependencias

| Puerto | Productor/implementación futura | Consumidor |
|---|---|---|
| `PlannerPort` | proveedor de planificación | servicio de producción |
| `ScriptWriterPort` | proveedor de guion | servicio de producción/escenas |
| `ScenePlannerPort` | proveedor de escenas | servicio de producción |
| `AssetProviderPort` | fuente local/remota/generativa | etapa de recursos |
| `NarrationProviderPort` | motor de voz | etapa de narración |
| `SubtitleProviderPort` | alineador/transcriptor | etapa de subtítulos |
| `MusicProviderPort` | catálogo/generador autorizado | etapa de música |
| `EditorPort` | adaptador de editor profesional | etapas de timeline/render |
| `ClipHandoffPort` | adaptador al generador actual | etapa opcional posterior al render |
| `ArtifactStorePort` | registro y almacenamiento local | todas las etapas |
| `ProductionJobRepositoryPort` | repositorio durable | API, worker y servicios |

Todos los métodos con trabajo de infraestructura son asíncronos para no imponer un transporte. Importar un `Protocol` no ejecuta ese trabajo.

## Coordinación y ejecución

- `ProductionOrchestrator` recibe el estado actual y un resultado opcional; decide el nuevo estado,
  el siguiente `StageCommand` y los eventos. No ejecuta puertos ni contiene lógica de proveedor.
- `ProductionWorker` será infraestructura de ejecución: reclamará trabajos, entregará comandos a
  handlers y persistirá la decisión en Fase 2/3. No contendrá reglas de transición.
- `PlannerPort` tiene una sola capacidad especializada: producir `ProductionPlan`.
- `StageHandler` será el adaptador de aplicación que ejecute un comando usando el puerto propio de
  la etapa y devuelva `StageResult`.
- `ProductionJobRepositoryPort` abstrae persistencia; no selecciona etapas ni interpreta resultados.

El orquestador no depende de FastAPI, SQLite, DaVinci, MCP, FFmpeg, OpenCV o Codex. Los efectos
externos solo pueden aparecer en futuras implementaciones de puertos y handlers.

## Comandos y resultados de etapa

`StageCommand` describe qué etapa debe ejecutarse: identidad, trabajo, intento, clave de
idempotencia, artefactos de entrada, snapshot y timestamp UTC. No ejecuta la etapa.

`StageResult` describe el resultado de ese comando. Sus outcomes son `succeeded`,
`failed_transient`, `failed_permanent`, `needs_user_action` y `cancelled`. La identidad del
comando, trabajo y etapa se coteja antes de que el orquestador aplique el resultado.

La decisión es determinista cuando se conservan estado, resultado, configuración, reloj, UUID y
número de secuencia. Una clave de idempotencia identifica el intento; la deduplicación durable de
resultados llegará con persistencia en Fase 2.

## Eventos de dominio

Los eventos son contratos JSON de hechos ya decididos: creación/cola del trabajo, inicio/progreso/
éxito/fallo de etapa, reintento, acción requerida, cancelación y finalización. Comparten versión,
UUID, trabajo, tipo, timestamp UTC, secuencia, correlación, causación y metadata.

Estos modelos no publican, guardan ni entregan eventos. No son una cola durable ni un `EventBus`.
La Fase 2 deberá persistir decisión, estado, etapa y eventos de forma atómica antes de que un worker
produzca el siguiente efecto externo.

## Contrato transaccional de Fase 2

`OrchestrationDecisionStore` es el límite de escritura durable. Recibe el trabajo anterior, una
`OrchestrationDecision`, comando/resultado procesados opcionales y artefactos registrados. Usa una
única sesión para guardar todos los componentes y hace commit solamente después del último flush.
Una excepción revierte la decisión completa.

Las tablas son `production_jobs`, `production_stage_runs`, `stage_commands`, `stage_results`,
`production_events` y `production_artifacts`. Los modelos SQLAlchemy viven en infraestructura y no
se exponen al dominio.

`production_jobs.row_version` comienza en 1 y aumenta con cada actualización. Una escritura sobre
estado obsoleto produce `ProductionConcurrencyError`. La misma decisión puede repetirse cuando
todos sus IDs y contenidos coinciden; reutilizar ID, clave idempotente, ruta o secuencia con otro
contenido produce un conflicto explícito.

Los eventos persistidos forman un audit log ordenado y único por trabajo. No son una cola ni
implican publicación externa. La sesión, el commit, el rollback y el cierre pertenecen a
`ProductionUnitOfWork`; los repositorios nunca hacen commit por su cuenta.

## Contrato del runtime local después de Fase 3.5

`ProductionWorker` reclama mediante `ProductionLeaseManager`, delega en `ClaimedJobProcessor` y
libera en `finally`. Un ciclo produce y persiste exactamente una `OrchestrationDecision`.
`RuntimeStateReader` encapsula las lecturas SQL y rechaza más de un comando pendiente.
`LeaseRepository` encapsula SQL/transacciones de lease; el manager conserva solo política.

`ProductionExecutor` recibe un `StageCommand` y su `StageContext`, resuelve un `StageHandler`
mediante un registro por `ProductionStage` y devuelve un `StageExecutionOutput`. No abre sesiones
ni persiste. `StageContext` es información versionada, relativa y sin servicios. Los
handlers de esta fase son simuladores deterministas: generan contratos `StageResult` y `Artifact`,
pero no crean archivos ni llaman puertos externos.

La lease durable tiene propietario, vencimiento, heartbeat y versión. Mientras se ejecuta una
etapa, `ProductionHeartbeat` renueva la lease. Tras el vencimiento, otro worker puede reclamar el
trabajo `running` y continuar desde su comando no procesado. Un reintento solo vuelve a `queued`
cuando el `retry_at` del evento durable ha llegado. La API, el arranque automático y la ejecución
de proveedores siguen fuera del contrato de esta fase.

Las lecturas/persistencia síncronas pueden aislarse detrás de ejecutores threaded que crean sus
sesiones dentro de la operación. Las variantes inmediatas existen para pruebas deterministas.

## Versionado

- `schema_version` y `version` usan SemVer completo (`major.minor.patch`).
- Un cambio compatible y aditivo incrementa minor; una corrección de interpretación sin cambio de forma incrementa patch.
- Eliminar/renombrar campos, cambiar unidades, significado o enum incrementa major.
- Consumidores validan la versión antes de causar efectos externos.
- Los JSON persistidos conservan su versión original. Las migraciones se implementan como transformaciones explícitas, nunca modificando silenciosamente el significado.

## Reglas de rutas relativas

`Artifact.relative_path` y `EditPackage.output_relative_path`:

- deben ser relativas al directorio raíz del trabajo;
- no pueden ser absolutas POSIX ni Windows;
- no pueden incluir drive de Windows, `..`, NUL, segmentos `.` o vacíos;
- no incluyen `~` como mecanismo de expansión;
- se resuelven en infraestructura y, después de `resolve()`, se verifica que sigan dentro de `PROJECTS_DIR/<job_id>`.

El dominio solo valida la forma portable. La infraestructura debe realizar además la comprobación de contención y política de extensión/MIME antes de abrir un archivo.

## Idempotencia

- El `job_id` y los `scene_id` permanecen estables durante reintentos.
- Cada etapa futura calculará una clave a partir del trabajo, etapa, versión y hash de entradas normalizadas.
- Un artefacto `ready` solo se reutiliza si el archivo existe y su checksum coincide.
- Proyecto, timeline y salida usarán nombres internos deterministas derivados del UUID.
- `create_project`, `build_timeline` y `render` deben reconciliar estado antes de crear duplicados.
- Guardar un artefacto se hará mediante escritura temporal y publicación atómica dentro de la carpeta del trabajo.

## `ProductionJob` frente a `VideoProject`

`VideoProject`, ubicado en `backend/src/core/domain/entities/video_project.py`, representa el flujo histórico de un video existente hacia clips. `ProductionJob` representa una producción multi-etapa originada en un prompt.

No se heredan ni comparten estado porque tienen ciclos de vida, recuperación y artefactos distintos. Solo se relacionan en el handoff opcional: un `ProductionJob` terminado conserva el UUID del proyecto histórico en `clip_project_id`.

## Codex no es una dependencia de ejecución

Codex ayuda a desarrollar ORION, pero no aparece en modelos, puertos ni composición de runtime. ORION no envía prompts a Codex, no requiere que el usuario lo abra y no usa una sesión de Codex para controlar otras herramientas. Los proveedores futuros se conectarán mediante puertos explícitos y configuración propia del producto.

## DaVinci detrás de `EditorPort`

El dominio necesita editar y renderizar, no conocer una marca o protocolo. `EditorPort` ofrece:

- `validate_environment()`;
- `create_project()`;
- `build_timeline()`;
- `render()`;
- `inspect_render()`.

Una futura implementación podrá usar scripting de DaVinci o un puente MCP local autónomo. Encapsularlo permite probar con un fake, cambiar transporte/versiones y evitar que tipos o fallos del editor contaminen el dominio.

## Ejemplo resumido de `EditPackage`

El ejemplo validable completo está en `backend/tests/fixtures/production/edit_package_example.json`.

```json
{
  "schema_version": "1.0.0",
  "job_id": "10000000-0000-4000-8000-000000000001",
  "project_name": "ORION Vertical Demo",
  "timeline_name": "Vertical Short 01",
  "width": 1080,
  "height": 1920,
  "fps": 30.0,
  "duration_seconds": 20.0,
  "scenes": ["cuatro escenas concretas de cinco segundos"],
  "artifacts": ["cuatro imágenes", "narración", "música", "subtítulos"],
  "output_relative_path": "renders/orion_vertical_short.mp4",
  "render_preset": "orion_vertical_h264"
}
```

El JSON resumido es ilustrativo; las cadenas dentro de `scenes` y `artifacts` no validan como contrato. La fixture completa sí debe validarse mediante `EditPackage.model_validate_json()`.
