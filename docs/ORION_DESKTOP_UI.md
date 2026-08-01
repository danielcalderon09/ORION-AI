# ORION Desktop UI

## Propósito

ORION Desktop es el primer cliente gráfico nativo del pipeline local. Está
construido con PySide6 y está pensado para un único propietario en Windows. No
usa Electron, navegador, React, Flask, FastAPI ni un servidor local.

La interfaz no contiene lógica de producción. No crea artifacts, no modifica
manifests y no ejecuta FFmpeg/FFprobe. Toda generación continúa en
`backend/src/production`.

## Ejecución

Instalar el extra de escritorio:

```text
pip install -e ".[desktop]"
```

Iniciar la aplicación desde la raíz del repositorio:

```text
python -m backend.src.desktop
```

FFmpeg y FFprobe deben cumplir la misma configuración local documentada para
`local_simulated_e2e`. La UI no añade rutas, argumentos ni discovery propios.

## Arquitectura

La capa nueva vive en `backend/src/desktop` y depende hacia adentro de los
contratos públicos de Production:

```text
OrionMainWindow
  -> PipelineThread / JobsLoaderThread
    -> ProductionDesktopBackend
      -> ListProductionJobsService
      -> RetryProductionJobService
      -> LocalMvpApplication
        -> ProductionWorker / ProductionOrchestrator
        -> pipeline durable existente
```

`ProductionDesktopBackend` es un adaptador delgado. Para el panel lateral
compone exclusivamente engine, repositorio y `ListProductionJobsService`; así
listar trabajos no necesita construir renderers. Para generar o reanudar usa el
composition root existente, prepara el schema con Alembic y delega en
`LocalMvpApplication`.

La configuración de ejecución reutiliza `local_mvp_settings()` del CLI. Todos
los providers continúan simulados y el renderer continúa siendo FFmpeg, sin
duplicar esa política en la UI.

Fase 6C permite que la configuración tipada del backend seleccione OpenRouter
solo para SCRIPTING. La UI no cambia: no solicita ni guarda credenciales en
QSettings y arranca sin key en el modo `simulated` predeterminado. Una
configuración OpenRouter incompleta falla cerrada con un mensaje backend seguro.

## Ventana principal

La ventana `ORION AI` incluye:

- prompt multilinea;
- duración cerrada a 15, 30 o 60 segundos;
- formato 9:16, 16:9 o 1:1;
- botón **Generar Video**;
- panel lateral con los 25 trabajos más recientes;
- stage, estado, fecha y duración durable de cada trabajo;
- acción **Reanudar** o **Ver resultado** cuando corresponde;
- progreso de las doce etapas canónicas;
- panel final con MP4, duración, resolución, fps, codecs y SHA-256;
- apertura nativa del video y su carpeta mediante `QDesktopServices`.

El tema oscuro usa una paleta pequeña de grises, azul para acciones, verde para
etapas completas y rojo únicamente para errores.

## Progreso real

La ventana consume `LocalMvpProgress` directamente. Cada actualización contiene
stage, attempt, outcome, progress y artifacts emitidos por el backend. La UI
solo traduce esos valores a etiquetas visibles; no usa timers ni incrementos
simulados.

Las etiquetas mostradas son Planning, Script, Scenes, Visual Planning, Assets,
Video Clips, Narration, Music, Subtitles, Timeline, Rendering y Validation.

## Threading

Toda coroutine de Production se ejecuta dentro de un `QThread`:

- `JobsLoaderThread` carga el historial;
- `PipelineThread` crea o reanuda la producción y emite progreso;
- las señales Qt entregan datos inmutables de vuelta al GUI thread.

La ventana permanece responsiva durante operaciones largas. No se permite
cerrarla mientras un pipeline está activo, evitando abandonar o destruir el
worker Qt. La UI no llama `subprocess` ni crea event loops en el GUI thread.

## Jobs y resume

El panel usa `ListProductionJobsService`, cuya consulta ya ordena por fecha
descendente. La duración se lee del snapshot durable de configuración. Jobs en
cola o ejecución continúan desde su estado actual. Un click explícito sobre un
job FAILED/NEEDS_USER_ACTION invoca primero el servicio durable de retry y luego
resume. Jobs COMPLETED recuperan su resultado validado sin rerender.

## Resultado y apertura local

El resultado proviene de `LocalMvpReport.output`, que ya verifica
`FINAL_RENDER_VALIDATION`, `LONG_FORM_RENDER`, tamaño y SHA-256. La UI no vuelve
a interpretar manifests. Las rutas absolutas solo se usan en memoria para
mostrar y abrir archivos locales.

## Errores y logs

Las excepciones se registran mediante el logger Python en el thread de backend.
La ventana recibe únicamente un texto corto y sanitizado. Nunca muestra un
traceback, command line, environment o contenido de manifests.

## Preferencias locales

`QSettings("ORION", "ORION AI")` conserva automáticamente:

- última duración;
- último formato;
- última carpeta abierta;
- geometría/tamaño de la ventana.

Estas preferencias son exclusivamente visuales y no alteran jobs ni
fingerprints.

## Tests

Las pruebas usan el plugin Qt offscreen y un fake de la frontera
`DesktopBackend`. Cubren creación de ventana, carga de trabajos, progreso desde
contratos reales, finalización con datos del MP4 y errores sin traceback. No
crean artifacts de Production ni ejecutan multimedia.

## Limitaciones

- La calidad creativa continúa siendo simulada, igual que en el MVP local.
- No hay reproductor embebido; **Abrir video** usa la aplicación predeterminada
  de Windows.
- No hay cancelación interactiva ni edición del timeline.
- Solo se muestra la primera página de 25 trabajos.
- El frontend Electron histórico permanece en el repositorio, pero esta UI no
  lo importa ni lo ejecuta.
