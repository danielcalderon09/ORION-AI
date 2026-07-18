# Plan maestro de ORION: Prompt a video y Video a clips

Estado del documento: arquitectura objetivo y plan incremental. La Fase 1 fijó los contratos
centrales y la Fase 1.5 añade coordinación pura, estados y eventos sin infraestructura.

## 1. Objetivo del producto

ORION será una aplicación de escritorio con un backend local capaz de operar en dos modos independientes pero conectables:

1. **Prompt a video:** convierte una intención del usuario en un video largo editado profesionalmente y, de forma opcional, entrega ese render al generador de clips.
2. **Video a clips:** conserva el flujo actual que analiza un video existente y exporta clips verticales.

La prioridad arquitectónica es preservar el flujo operativo actual mientras el primer modo nace como un bounded context separado, con contratos versionados, persistencia durable y adaptadores reemplazables.

## 2. Responsabilidades de ORION, Codex y DaVinci

- **ORION** es el producto y el único sistema con el que interactúa el usuario final. Administra trabajos, estado, artefactos, proveedores, edición y handoff.
- **Codex** es una herramienta de desarrollo. No es un proveedor, un puerto, una dependencia, un proceso auxiliar ni una API de runtime de ORION.
- **DaVinci Resolve** será el motor profesional de edición y render. ORION lo controlará mediante una implementación de `EditorPort`; el dominio no conocerá scripting, MCP ni objetos propios de Resolve.

El adaptador futuro podrá usar la API de scripting de Resolve o un servidor MCP local autónomo. En ambos casos, ORION será el cliente/orquestador y no dependerá de una sesión de Codex.

## 3. Hechos verificados que condicionan el diseño

- Backend FastAPI/Python y frontend Electron/React/TypeScript.
- El flujo operativo se concentra en `backend/src/api/v1/video_controller.py`.
- Los proyectos actuales se guardan bajo `settings.PROJECTS_DIR`, cuyo valor predeterminado es `~/OrionProjects`.
- El endpoint actual usa `BackgroundTasks` y `_progress_store`, un diccionario en memoria.
- `Sprint5Orchestrator` se construye en el controlador, pero el endpoint operativo no lo invoca para generar los clips. No se adoptará como núcleo del nuevo flujo.
- No hay persistencia durable del progreso operativo ni integración existente con DaVinci.
- El frontend tiene deuda TypeScript y no se modificará hasta las fases de interfaz.
- SQLAlchemy y Alembic ya figuran como dependencias, pero esta fase no crea base de datos ni migraciones.

## 4. Arquitectura objetivo

El sistema se separará en dos bounded contexts y una capa de aplicación común:

- `production`: prompt, planes, escenas, artefactos, ejecución por etapas y edición profesional.
- `video/clips` existente: ingestión de un video y creación de clips verticales.
- Adaptadores: persistencia, sistema de archivos, proveedores intercambiables, editor y handoff.
- Worker local durable: ejecuta etapas fuera del ciclo HTTP y recupera trabajos interrumpidos.

La API será un plano de control, no el lugar donde ocurre el procesamiento. Los controladores validarán solicitudes y llamarán casos de uso; no acumularán lógica de medios.

`ProductionOrchestrator` será el coordinador determinista del pipeline. Decidirá la siguiente
etapa, emitirá su comando y aplicará resultados para producir un nuevo estado lógico y eventos.
No ejecutará capacidades externas. El futuro `ProductionWorker` obtendrá trabajos y ejecutará
comandos, pero delegará las decisiones de negocio al orquestador y la capacidad concreta a un
`StageHandler` respaldado por puertos.

## 5. Diagrama textual

```text
Electron / React
  ├─ Modo 1: Prompt a video
  │    └─ FastAPI /production/jobs (futuro)
  │         └─ ProductionApplicationService
  │              ├─ ProductionJobRepositoryPort ── SQLite/SQLAlchemy
  │              ├─ ArtifactStorePort ───────────── registro + filesystem aislado
  │              └─ cola durable local
  │                   └─ ProductionWorker
  │                        ├─ ProductionOrchestrator (decisiones puras)
  │                        └─ StageHandler (ejecución de un StageCommand)
  │                             ├─ PlannerPort
  │                             ├─ ScriptWriterPort
  │                             ├─ ScenePlannerPort
  │                        ├─ AssetProviderPort
  │                        ├─ NarrationProviderPort
  │                        ├─ MusicProviderPort
  │                        ├─ SubtitleProviderPort
  │                        ├─ EditorPort
  │                        │    └─ DaVinciEditorAdapter (scripting o puente MCP local)
  │                        ├─ FFprobeRenderInspector
  │                        └─ ClipHandoffPort (opcional)
  │                             └─ fachada del motor actual de clips
  │
  └─ Modo 2: Video a clips
       └─ endpoints actuales ── flujo operativo actual (preservado)
```

## 6. Módulos nuevos propuestos

```text
backend/src/production/
  domain/                 contratos y reglas puras
  application/
    ports/                dependencias abstractas
    orchestration/        decisiones, transiciones y orden de etapas
    commands/             StageCommand versionado
    results/              StageResult versionado
    events/               eventos de dominio serializables
    services/             casos de uso y máquina de estados (Fase 3)
  infrastructure/
    persistence/          repositorios SQLAlchemy (Fase 2)
    artifacts/            almacenamiento local seguro (Fase 2)
    worker/               leasing, reintentos y recuperación (Fase 3)
    editors/davinci/      implementación de EditorPort (Fase 4)
    providers/            proveedores concretos por capacidad (Fases 5-8)
    clips/                adaptador al generador existente (Fase 10)
backend/src/api/v1/production_controller.py  API futura del modo 1
frontend/src/.../production/                UI futura del modo 1
```

La Fase 1 solo incorpora `domain`, `application/ports` e `infrastructure/__init__.py`; no registra rutas ni inicia procesos.

## 7. Reutilización y límites

### Reutilizar sin cambios inicialmente

- `backend/src/infrastructure/config/settings.py` como fuente de configuración.
- `settings.PROJECTS_DIR` como raíz configurable de proyectos, siempre mediante resolución segura.
- FFmpeg/FFprobe instalados por el producto, para inspección y utilidades técnicas futuras.
- El endpoint y motor operativo actual de clips durante las primeras nueve fases.

### Adaptar detrás de una interfaz

- El generador actual de clips: extraer posteriormente un caso de uso invocable por `ClipHandoffPort`, preservando el contrato HTTP actual.
- Progreso: pasar de `_progress_store` a eventos/etapas durables sin cambiar de golpe el endpoint histórico.
- Registro de archivos: evolucionar carpetas implícitas a `Artifact` con checksum y ruta relativa.
- Configuración: añadir banderas y snapshots por trabajo.

### No reutilizar como núcleo del nuevo flujo

- `video_controller.py` como orquestador de producción.
- `BackgroundTasks` como cola durable.
- `_progress_store` como fuente de verdad.
- `Sprint5Orchestrator` como orquestador del modo Prompt a video.
- Rutas absolutas almacenadas como contratos entre módulos.

## 8. Contratos entre módulos

- `ProductionJob`: identidad, estado, etapa y referencias principales.
- `ProductionPlan`: intención creativa y parámetros técnicos validados.
- `ScenePlan`: unidad narrativa planificada, todavía independiente de un recurso concreto.
- `Artifact`: registro versionado de un archivo perteneciente a un trabajo.
- `EditPackage`: timeline completamente resuelta que consume `EditorPort`.
- Puertos de planificación, guion, escenas, recursos, narración, subtítulos, música, edición, artefactos, repositorio y clips.

Los contratos cruzan límites mediante JSON UTF-8 y versiones SemVer. Los UUID se serializan como cadenas canónicas. Un adaptador debe validar el contrato al recibirlo y no aceptar campos desconocidos sin una evolución explícita.

## 9. Diseño de modelos centrales

### ProductionJob

Agregado durable del modo Prompt a video. Contiene `job_id`, prompt, estado, etapa, timestamps, snapshot de configuración, error estructurado y referencias opcionales al render largo y al proyecto de clips. No contiene objetos de FastAPI, tareas en memoria ni instancias de proveedores.

### ProductionPlan

Plan creativo/técnico versionado: plataforma, idioma, duración, lienzo, FPS, estilo, audiencia, narración, música, decisión de generar clips y escenas. La duración declarada debe coincidir con la suma de escenas.

### ScenePlan

Escena ordenada con duración, narración, descripción visual, consulta de recurso, tipo de recurso, movimiento, transición y texto opcional. `scene_id` es estable para reintentos.

### Artifact

Manifiesto de un archivo: UUID, trabajo propietario, tipo, ruta relativa segura, MIME, estado, tamaño, hash, metadatos técnicos y procedencia. El archivo y su registro se publican de forma atómica en fases posteriores.

### EditPackage

Contrato versionado para cualquier editor. Incluye lienzo, FPS, duración, escenas con recursos concretos, inventario de artefactos, narración/música/subtítulos opcionales, preset y salida relativa. No contiene comandos, código de automatización ni tipos de DaVinci.

## 10. Estados completos del trabajo

Estados de `ProductionJob`: `created`, `queued`, `running`, `waiting_for_retry`, `needs_user_action`, `cancel_requested`, `cancelled`, `completed`, `failed`.

Etapas: `created`, `planning`, `scripting`, `scene_planning`, `acquiring_assets`, `generating_narration`, `preparing_music`, `generating_subtitles`, `building_timeline`, `rendering_long_form`, `validating_render`, `handing_off_to_clips`, `waiting_for_clips`, `completed`.

Las transiciones serán controladas por un servicio de aplicación. `failed` y `cancelled` son estados, no etapas. Cada etapa tendrá en persistencia futura intentos, timestamps, error, progreso y clave de idempotencia.

La división de responsabilidades será:

- `ProductionOrchestrator`: decide transiciones, siguiente etapa, comandos y eventos.
- `ProductionWorker`: reclama y ejecuta trabajo; no contiene reglas de negocio.
- `PlannerPort`: produce únicamente `ProductionPlan`.
- `StageHandler`: traduce un `StageCommand` a la invocación del puerto especializado y devuelve
  `StageResult`.
- `ProductionJobRepositoryPort`: abstrae carga y guardado durable; no decide transiciones.

Cada etapa se expresa mediante un comando y un resultado. Con el mismo trabajo, resultado,
configuración, reloj y fábrica de UUID, el orquestador debe producir la misma decisión.

## 11. Persistencia, recuperación y worker local

En Fase 2 se propone SQLite con SQLAlchemy/Alembic, adecuado para la aplicación local y ya presente como dependencia. Tablas previstas: `production_jobs`, `production_stage_runs`, `production_artifacts` y `production_events`. JSON de planes y snapshots se guardará versionado; índices cubrirán estado, etapa y siguiente intento.

La Fase 2 materializa además `stage_commands` y `stage_results`. Una
`OrchestrationDecisionStore` guarda en una sola transacción el trabajo actualizado, el resultado
procesado, el comando siguiente, las ejecuciones de etapa, artefactos y eventos. Los repositorios
solo hacen `flush`; el commit pertenece a una unidad de trabajo compartida. Cualquier error causa
rollback completo.

`production_jobs.row_version` implementa optimistic locking. Los comandos se deduplican por UUID
y `idempotency_key`, los resultados usan `command_id` como identidad persistente y los eventos son
únicos por `(job_id, sequence_number)`. El audit log persistido no es un `EventBus`: no publica nada
fuera de la transacción.

El worker de Fase 3 reclamará un trabajo mediante lease transaccional, registrará heartbeat y ejecutará una etapa por vez. Al reiniciar:

1. libera leases vencidos;
2. reconcilia registros con artefactos publicados;
3. reanuda desde la última etapa completada;
4. no repite una salida si su clave y checksum ya son válidos.

DaVinci se tratará como recurso exclusivo por defecto: concurrencia uno para las etapas de edición/render, aunque planificación y adquisición puedan paralelizarse más adelante.

Los eventos de dominio producidos por el orquestador describen hechos, pero no constituyen una
cola durable ni un `EventBus`. La persistencia atómica de trabajos, comandos, resultados, etapas
y eventos llegará en Fase 2. Los efectos externos permanecerán detrás de puertos.

## 12. Adaptador de DaVinci y control sin Codex

`DaVinciEditorAdapter` implementará `EditorPort` y traducirá `EditPackage` a operaciones del editor. Se evaluarán dos transportes detrás del mismo adaptador:

- API de scripting de Resolve mediante un proceso puente local controlado por ORION.
- Servidor MCP local empaquetado como componente de ORION, sin Codex y con protocolo restringido.

La selección será configuración del adaptador, no del dominio. El puente deberá validar versión, disponibilidad, proyecto/timeline, imports, render preset y respuesta. Los identificadores externos serán opacos. Ninguna etapa llamará scripts construidos desde texto del usuario ni usará `shell=True`.

## 13. Idempotencia, reintentos y cancelación

### Idempotencia

- Clave por etapa: hash de `job_id + stage + contract_version + normalized_inputs`.
- Nombres deterministas de proyecto, timeline y salida derivados del UUID, no del prompt.
- Escritura a archivo temporal dentro del directorio del trabajo y publicación atómica.
- Un artefacto `ready` con checksum válido evita regeneración.
- El adaptador reconcilia antes de crear proyecto, timeline o render.

### Reintentos

- Clasificación explícita: transitorio, configuración/acción del usuario y permanente.
- Backoff exponencial acotado con jitter y máximo por etapa.
- Cada intento queda registrado; no se reintenta validación, ruta insegura o contrato inválido.
- Render y edición se reconcilian antes de repetir para evitar duplicados.

### Cancelación

- La API persiste `cancel_requested`.
- El worker consulta antes de cada etapa y en puntos cooperativos.
- Una operación externa solo se interrumpe si el adaptador puede hacerlo de forma segura; en caso contrario se espera, se inspecciona y se descarta/publica según estado.
- Los artefactos ya válidos se conservan para auditoría y posible reanudación.

## 14. Progreso

El progreso será durable y derivado de etapas, no de un porcentaje mutable aislado. Cada etapa tendrá peso, estado, unidades completadas y mensaje seguro. La API devolverá snapshot y secuencia de eventos; el frontend podrá consultar o escuchar eventos. El progreso nunca disminuirá dentro de un intento y un reintento se mostrará explícitamente.

## 15. Validación del render con FFprobe

Después de renderizar, un inspector independiente ejecutará FFprobe con una lista fija de argumentos, sin shell. Validará:

- existencia, tamaño y legibilidad;
- contenedor y streams de video/audio esperados;
- ancho, alto y FPS dentro de tolerancia;
- duración frente a `EditPackage` dentro de tolerancia definida;
- ausencia de duración cero o stream truncado;
- checksum SHA-256 antes de marcar `LONG_FORM_RENDER` como `ready`.

Solo un render validado podrá pasar al generador de clips.

## 16. Handoff al generador de clips existente

En Fase 10, `ClipHandoffPort` recibirá `ProductionJob` y el `Artifact` de render largo validado. Una implementación local llamará una fachada extraída del flujo existente, no hará una petición HTTP a sí mismo ni duplicará el motor. El nuevo proyecto de clips conservará UUID y carpeta propios; su ID quedará en `clip_project_id`. El handoff será opcional según `generate_clips_after_render`.

Hasta esa fase, el endpoint actual y `video_controller.py` no cambian.

## 17. Frontend futuro

La navegación principal ofrecerá dos tarjetas/modos. Prompt a video tendrá formulario, resumen del plan, escenas, artefactos, progreso durable, acciones de cancelar/reintentar y resultados. Video a clips conservará el flujo actual. Se reutilizarán componentes visuales de progreso y resultado solo después de corregir el type checking y separar sus contratos de API.

La bandera `ORION_PROMPT_VIDEO_ENABLED`, desactivada por defecto, impedirá mostrar UI y registrar rutas públicas hasta que cada corte vertical sea seguro.

## 18. Seguridad y validación de rutas

- Todos los contratos usan rutas relativas, sin raíz, drive, `..`, segmentos vacíos ni NUL.
- Una ruta se resuelve con `resolve()` contra el directorio UUID del trabajo y se verifica que permanezca dentro de él.
- Nombres externos se convierten a IDs/nombres internos; prompt y nombres originales son metadatos, nunca paths ni comandos.
- Extensión, MIME, tamaño, duración y contenido se validan antes del registro.
- Subprocesos reciben listas de argumentos fijas, sin interpolación de shell.
- Descargas futuras tendrán allowlist de esquema/host, límites, timeout y tamaño máximo.
- La API local requerirá política explícita de origen/autenticación antes de exponerse fuera de loopback.
- Secretos futuros se referenciarán por configuración segura y nunca se copiarán al snapshot o logs.

## 19. Feature flags

- `ORION_PROMPT_VIDEO_ENABLED=false`: interruptor maestro, introducido en Fase 1.
- Banderas futuras: API, worker, editor, proveedores y handoff, todas independientes y apagadas hasta su fase.
- Las banderas se evalúan al componer la aplicación; no alteran trabajos ya en ejecución sin una transición controlada.

## 20. Estrategia de pruebas

- Unitarias puras para modelos, máquina de estados, idempotencia y seguridad de rutas.
- Contrato para cada adaptador usando una suite común.
- Repositorios contra SQLite temporal y migraciones hacia adelante/atrás.
- Worker con reloj y proveedores simulados, incluyendo crash/recovery/cancelación.
- Integración de DaVinci opt-in y etiquetada, nunca en suite determinista normal.
- Render sintético local pequeño + FFprobe en integración opt-in.
- Caracterización del endpoint actual antes de extraer su fachada.
- Frontend: type check, pruebas de componentes y E2E de ambos modos.

## 21. Plan de rollback global

Cada fase queda detrás de bandera y en módulos nuevos. El rollback operativo consiste en apagar la bandera y volver al modo Video a clips. Las migraciones futuras deben ser aditivas; no se elimina información histórica en el mismo despliegue que introduce un reemplazo. El motor actual se conserva hasta completar y verificar la Fase 10.

## 22. Fases de implementación

### Fase 1 — Red de seguridad y contratos centrales

- **Objetivo:** fijar límites de dominio y caracterizar el flujo actual sin cambiarlo.
- **Alcance:** modelos versionados, enums, validación de rutas/duración, puertos, fixture, pruebas, documentación y bandera apagada.
- **Archivos previstos:** `backend/src/production/domain/*`, `backend/src/production/application/ports/*`, `backend/tests/unit/production/*`, `backend/tests/characterization/test_current_clip_api.py`, fixture, settings y estos documentos.
- **Dependencias:** Pydantic, pytest y librería estándar ya presentes.
- **Aceptación:** paquete importable; JSON validado; rutas inseguras rechazadas; puertos sin IO; flag `false`; caracterización ligera; controlador sin cambio funcional.
- **Pruebas:** unitarias de contratos, import safety, settings y caracterización mockeada.
- **Riesgos:** contratos prematuros o deuda existente descubierta por caracterización.
- **Rollback:** retirar solo módulos/documentos/tests nuevos y la propiedad de settings; no hay datos ni migraciones.
- **No se hará:** API, persistencia, worker, proveedor, DaVinci, render ni UI.

### Fase 1.5 — Coordinación del pipeline, estados y eventos

- **Objetivo:** fijar decisiones puras antes de diseñar la persistencia.
- **Alcance:** `ProductionOrchestrator`, comandos/resultados, eventos, política de transiciones y
  registro ordenado de etapas.
- **Archivos previstos:** `production/application/{orchestration,commands,results,events}/*` y
  pruebas unitarias deterministas.
- **Dependencias:** contratos de Fase 1, Pydantic y biblioteca estándar.
- **Aceptación:** mismas entradas controladas producen la misma decisión; no hay IO ni adaptadores.
- **Pruebas:** validación estricta, transiciones, orden, resultados, cancelación y determinismo.
- **Riesgos:** cerrar prematuramente semántica de reintentos antes del diseño transaccional.
- **Rollback:** retirar los módulos de aplicación nuevos; no existen datos ni efectos externos.
- **No se hará:** persistencia, API, worker, `StageHandler` concreto, proveedores o editor.

### Fase 2 — Persistencia durable de ProductionJob, etapas y artefactos

- **Objetivo:** hacer durable la fuente de verdad del modo 1.
- **Alcance:** modelos SQLAlchemy, mappers explícitos, repositorios, unidad de trabajo,
  almacenamiento atómico de decisiones y migración inicial aditiva.
- **Archivos previstos:** `production/infrastructure/persistence/*`, `backend/src/infrastructure/database/*`, `backend/migrations/*`, tests de repositorio.
- **Dependencias:** contratos Fase 1; SQLAlchemy/Alembic existentes; decisión final de ubicación SQLite.
- **Aceptación:** CRUD transaccional, `row_version`, idempotencia durable, eventos/etapas
  recuperables, rollback completo y migración reversible.
- **Pruebas:** SQLite temporal, constraints, rollback transaccional y upgrade/downgrade.
- **Riesgos:** mezclar `VideoProject` con `ProductionJob` o almacenar paths absolutos.
- **Rollback:** downgrade de migración antes de publicar trabajos reales; bandera permanece apagada.
- **No se hará:** endpoints, worker o procesamiento.

### Fase 3 — API de trabajos y worker local con etapas simuladas

- **Objetivo:** probar ciclo durable completo sin medios ni servicios externos.
- **Alcance:** crear/listar/consultar/cancelar/reintentar, leasing, heartbeat y proveedores simulados.
- **Archivos previstos:** `production/application/services/*`, `production/infrastructure/worker/*`, `api/v1/production_controller.py`, composición y tests.
- **Dependencias:** repositorios Fase 2 y política de arranque/apagado de Electron/backend.
- **Aceptación:** trabajo simulado sobrevive reinicio, no duplica etapas y reporta progreso durable.
- **Pruebas:** API con DB temporal, crash/recovery, lease vencido, cancelación y reintento.
- **Riesgos:** doble ejecución y shutdown incompleto.
- **Rollback:** apagar flags de API/worker y conservar tablas aditivas.
- **No se hará:** recursos reales, DaVinci o exposición de UI final.

### Fase 4 — Integración mínima ORION → DaVinci con recursos locales

- **Objetivo:** validar `EditorPort` con imágenes/audio locales y un render controlado.
- **Alcance:** adaptador, detección de entorno, proyecto, timeline y render mínimo; transporte elegido tras spike.
- **Archivos previstos:** `production/infrastructure/editors/davinci/*`, configuración específica y tests opt-in.
- **Dependencias:** DaVinci instalado/configurado, documentación/licencia de scripting y fixture local.
- **Aceptación:** un `EditPackage` fixture crea/reconcilia proyecto y render sin Codex.
- **Pruebas:** contratos con fake; integración manual opt-in; repetición idempotente.
- **Riesgos:** versiones de Resolve, bloqueo de UI, APIs no disponibles y recursos externos exclusivos.
- **Rollback:** apagar flag del editor; adaptador no registrado; datos de trabajo conservados.
- **No se hará:** planificación IA, descarga de recursos o montaje completo de producción.

### Fase 5 — Planificador, guion y escenas con proveedor intercambiable

- **Objetivo:** producir contratos creativos mediante adaptadores sustituibles.
- **Alcance:** servicios de aplicación, provider config, validación/reparación limitada y simulador determinista.
- **Archivos previstos:** `production/infrastructure/providers/planning/*`, servicios y tests de contrato.
- **Dependencias:** selección/autorización posterior de proveedor; esquemas Fase 1.
- **Aceptación:** prompt produce plan/guion/escenas válidos y versionados; proveedor se cambia por configuración.
- **Pruebas:** golden JSON, salida inválida, timeout, retry y fake offline.
- **Riesgos:** resultados no deterministas, coste y drift de modelo.
- **Rollback:** volver al proveedor simulado o desactivar etapa.
- **No se hará:** voz, recursos visuales o música.

### Fase 6 — Narración y subtítulos

- **Objetivo:** generar voz y subtítulos sincronizados mediante puertos.
- **Alcance:** adapters, alineación, registro de artefactos y validación temporal.
- **Archivos previstos:** `providers/narration/*`, `providers/subtitles/*`, validadores y tests.
- **Dependencias:** decisión posterior de proveedores; plan/guion estables.
- **Aceptación:** audio y subtítulos válidos, relativos, checksummed y coherentes con duración.
- **Pruebas:** fakes, timing, caracteres, silencio, fallo/reintento y archivos pequeños opt-in.
- **Riesgos:** desincronización, pronunciación y licencias.
- **Rollback:** desactivar adaptadores y conservar script/plan.
- **No se hará:** adquisición visual o música.

### Fase 7 — Adquisición o generación de imágenes y videos

- **Objetivo:** resolver cada escena a recursos visuales registrados.
- **Alcance:** proveedores, caché, atribución/licencia, validación y selección.
- **Archivos previstos:** `providers/assets/*`, políticas de descarga y tests de contrato.
- **Dependencias:** decisiones de fuentes y credenciales futuras; seguridad de red.
- **Aceptación:** cada escena tiene un recurso válido o un error accionable; sin escape de carpeta.
- **Pruebas:** MIME falso, límites, timeout, duplicados, checksum y fake offline.
- **Riesgos:** contenido inseguro, licencias, SSRF, tamaño y coste.
- **Rollback:** proveedor local/manual y apagado de descargas.
- **No se hará:** mezcla musical ni handoff a clips.

### Fase 8 — Música y mezcla

- **Objetivo:** preparar música licenciada/generada y reglas de mezcla reproducibles.
- **Alcance:** `MusicProviderPort`, niveles, ducking, fades y contrato de stems.
- **Archivos previstos:** `providers/music/*`, planificación de mezcla y tests.
- **Dependencias:** política de licencias/proveedor y narración terminada.
- **Aceptación:** música registrada y paquete de edición con mezcla validada.
- **Pruebas:** duración, loudness objetivo, ausencia de clipping y fake determinista.
- **Riesgos:** derechos, volumen, codecs y calidad perceptual.
- **Rollback:** video sin música o pista local autorizada.
- **No se hará:** cambios al motor de clips.

### Fase 9 — Render largo y validación automática

- **Objetivo:** producir un render largo confiable antes de publicarlo.
- **Alcance:** ejecución final de editor, inspector FFprobe, checksum y publicación atómica.
- **Archivos previstos:** `editors/*`, `artifacts/render_inspector.py`, worker y tests opt-in.
- **Dependencias:** Fases 4 y 6-8; FFprobe disponible.
- **Aceptación:** solo renders técnicamente válidos alcanzan estado ready/completed.
- **Pruebas:** probes simulados, archivo truncado, resolución/FPS/duración y render pequeño opt-in.
- **Riesgos:** render bloqueado, disco lleno, tolerancias incorrectas.
- **Rollback:** desactivar publicación/render y retener `EditPackage` para reintento.
- **No se hará:** generación automática de clips.

### Fase 10 — Handoff al generador actual de clips

- **Objetivo:** conectar opcionalmente el render validado sin duplicar el motor.
- **Alcance:** extraer fachada/caso de uso del controlador, adaptador `ClipHandoffPort` y correlación de IDs.
- **Archivos previstos:** nuevo servicio en contexto de clips, adaptador `production/infrastructure/clips/*`, cambios mínimos al controlador y tests.
- **Dependencias:** caracterización ampliada del endpoint y render ready de Fase 9.
- **Aceptación:** endpoint histórico conserva contrato; el mismo motor acepta render largo; handoff idempotente.
- **Pruebas:** regresión HTTP, integración mockeada, duplicado/reinicio y fallo del clip job.
- **Riesgos:** fuerte acoplamiento actual en `video_controller.py` y progreso no durable del modo 2.
- **Rollback:** desactivar handoff y mantener descarga del render largo.
- **No se hará:** rediseño total del motor de clips ni adopción de Sprint5Orchestrator.

### Fase 11 — Interfaz completa con los dos modos

- **Objetivo:** exponer ambos recorridos en Electron/React con contratos claros.
- **Alcance:** navegación, formularios, progreso, escenas, cancelación/reintento, resultados y feature flags.
- **Archivos previstos:** módulos frontend de producción, cliente API tipado, rutas/componentes y tests E2E.
- **Dependencias:** API estable y reducción previa de errores TypeScript relevantes.
- **Aceptación:** usuario opera ambos modos sin Codex y recupera un trabajo tras reiniciar UI.
- **Pruebas:** type check objetivo, componentes, accesibilidad y E2E con backend simulado.
- **Riesgos:** deuda TypeScript, estados divergentes y exceso de opciones.
- **Rollback:** ocultar modo 1 por flag; modo 2 permanece disponible.
- **No se hará:** apertura pública/remota de la API.

### Fase 12 — Endurecimiento, empaquetado, recuperación y seguridad

- **Objetivo:** preparar uso sostenido y distribución confiable.
- **Alcance:** lifecycle, backups, cuotas, limpieza segura, autenticación local, telemetría opt-in, diagnóstico y actualizaciones.
- **Archivos previstos:** empaquetado Electron/backend, seguridad, observabilidad, recovery y runbooks.
- **Dependencias:** todas las fases anteriores y matriz de plataformas/versiones.
- **Aceptación:** recuperación probada, instalación limpia, actualización/rollback, límites y auditoría de seguridad.
- **Pruebas:** soak, interrupción de energía simulada, disco lleno, upgrade, permisos y análisis de dependencias.
- **Riesgos:** diferencias de plataforma, pérdida de datos y superficie del editor/puentes locales.
- **Rollback:** instalador conserva versión anterior y DB compatible; flags desactivan capacidades nuevas.
- **No se hará:** ampliar proveedores sin su propia revisión de seguridad/coste.

## 23. Decisiones pendientes

1. API de scripting directa frente a puente MCP local autónomo para DaVinci, tras un spike de Fase 4.
2. Ubicación/versionado de SQLite y política de backup en la aplicación empaquetada.
3. Política de retención/cuotas de artefactos por trabajo.
4. Proveedores futuros de planificación, voz, recursos y música; ninguno se integra en esta ejecución.
5. Tolerancias exactas de duración/FPS por preset de render.
6. Estrategia de autenticación si FastAPI deja de escuchar solo en loopback.
