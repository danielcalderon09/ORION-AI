# Production End-to-End Local MVP (Fase 6A)

## Propósito y alcance

La Fase 6A demuestra un flujo local completo desde una idea en lenguaje natural
hasta un MP4 validado. ORION sigue siendo una herramienta personal para Windows:
no es SaaS, no expone un API público, no usa workers remotos y no incorpora
frontend, autenticación, pagos ni cloud.

Desde Fase 6C, solo SCRIPTING puede configurarse explícitamente con OpenRouter.
El default continúa siendo `simulated`; una key sola no autoriza gasto y el
modo local conserva imagen, video, narración, música y SFX simulados. La
activación controlada está documentada en `PRODUCTION_OPENROUTER_SCRIPTING.md`.

`local_simulated_e2e` usa contenido creativo simulado y determinista para probar
la integración técnica. Ese contenido no representa calidad final de IA.

## Flujo exacto

El entry point crea un `ProductionJob` normal y usa el registry, el worker, el
orquestador y la política de transiciones existentes:

1. `PLANNING` — plan durable simulado.
2. `SCRIPTING` — guion durable simulado.
3. `SCENE_PLANNING` — escenas y shots durables simulados.
4. `VISUAL_ASSET_PLANNING` — especificaciones visuales durables simuladas.
5. `ACQUIRING_ASSETS` — imágenes locales válidas y manifest.
6. `GENERATING_VIDEO_CLIPS` — clips MP4/H.264 locales técnicamente válidos.
7. `GENERATING_NARRATION` — WAV PCM simulado por escena.
8. `PREPARING_MUSIC` — música/SFX simulados solo cuando el guion los solicita.
9. `GENERATING_SUBTITLES` — SRT UTF-8 durable derivado del guion.
10. `BUILDING_TIMELINE` — plan y manifest de composición.
11. `RENDERING_LONG_FORM` — render local real con FFmpeg.
12. `VALIDATING_RENDER` — aceptación independiente con FFprobe y
    `FINAL_RENDER_VALIDATION`.
13. `COMPLETED` — estado final del job.

No se omite ni se invoca directamente ningún handler. Los artifacts inmediatos
se propagan mediante `StageResult.output_artifact_ids`; los readers durables
seleccionan únicamente los tipos históricos que necesitan. La validación final
recibe exactamente `LOCAL_RENDER_REQUEST`, `FFMPEG_EXECUTION_PLAN`,
`RENDER_EXECUTION_MANIFEST` y `LONG_FORM_RENDER`.

## Componentes simulados y reales

Son simulados/offline: planificación, guion, escenas, planificación visual,
adquisición de imágenes, contenido creativo de clips, narración tonal,
música/SFX y subtítulos. Los archivos de imagen, video, audio y SRT son bytes
locales reales y decodificables; no son placeholders vacíos.

Son reales: generación técnica de clips locales, composición FFmpeg,
inspección FFprobe, promoción atómica del MP4, checksum y validación final.
DaVinci Resolve y MCP no se usan.

## Entry point

Desde la raíz del repositorio:

```text
python -m backend.src.production.cli.generate_video --prompt "Explica en un video corto tres curiosidades sobre Marte." --mode local_simulated_e2e --output-summary
```

Argumentos admitidos:

- `--prompt` para un job nuevo;
- `--title` opcional;
- `--target-duration` entre 4 y 30 segundos;
- `--aspect-ratio` (`9:16`, `16:9` o `1:1`);
- `--project-id` local y seguro;
- `--resume-job-id` para continuar un job existente;
- `--output-summary` para JSON compacto.

No acepta rutas de output, argumentos FFmpeg, filtros, comandos, secrets ni
manipulación de stages.

## Perfil MVP predeterminado

El perfil `1.0.0` usa español, 8 segundos, dos escenas, formato vertical
360×640, 24 fps, narración simulada, subtítulos deterministas y MP4 con
H.264/AAC/yuv420p. Música y SFX siguen siendo opcionales. Las configuraciones
de producción existentes no se cambian globalmente.

## Dependencias locales y configuración

Python y la instalación del proyecto son obligatorios. FFmpeg y FFprobe deben
estar disponibles por la resolución controlada ya existente, mediante PATH o
las variables tipadas `ORION_FFMPEG_PATH`/`ORION_FFPROBE_PATH`. El modo fuerza
todos los providers creativos a `simulated` y `ORION_RENDERER=ffmpeg`. Si falta
un binario, falla claramente: nunca cae a `dry_run` y nunca reporta COMPLETED.

El CLI usa la base SQLite local de ORION bajo `ORION_HOME`, ejecuta el bootstrap
de Alembic existente y almacena media bajo `PROJECTS_DIR`.

## Job, idempotencia y loop

La creación pasa por `CreateProductionJobService`. El client request ID es un
SHA-256 estable del prompt normalizado, título, project ID y perfil. Repetir la
misma solicitud explícita devuelve el mismo job.

El loop consulta el job, ejecuta un ciclo del worker canónico y vuelve a leer el
estado durable. Se detiene en COMPLETED, FAILED, CANCELLED, retry programado,
intervención manual o al alcanzar 50 iteraciones. El límite no altera ni borra
el job. Cada avance informa stage, attempt, outcome, progreso, cantidad de
artifacts y código seguro de error.

## Resume y recuperación

`--resume-job-id <uuid>` continúa desde el estado persistido. No recrea stages
validados ni regenera artifacts recuperables. Un job COMPLETED resuelve su
`FINAL_RENDER_VALIDATION` y MP4 inmediatamente; no ejecuta worker, FFmpeg ni
FFprobe. Un job FAILED se informa sin reset automático. Un retry futuro o una
intervención manual detienen el loop con una acción local recomendada.

## Resultado y diagnóstico

Al completar, el CLI muestra job ID, estado, ruta relativa, ruta absoluta solo
en terminal, tamaño, SHA-256, duración, resolución, fps, codecs, artifact de
validación y tiempo local. Antes de informar éxito vuelve a comprobar el
manifest final, su checksum, el artifact `LONG_FORM_RENDER` y el archivo.

En fallo muestra job, stage, estado, código, retryability, intento reciente,
artifacts conservados y acción recomendada. No imprime manifests completos,
payloads ni traceback por defecto.

## Pruebas E2E

La prueba pura crea un job con el servicio real, usa registry, worker,
orquestador y persistencia SQLite reales, y verifica las 12 transiciones y la
propagación exacta de provenance. Sus doubles de render prueban orquestación;
no prueban un MP4 real.

La prueba real usa workspace/SQLite temporales, los providers simulados y
FFmpeg/FFprobe reales. Solo se omite cuando falta uno de los binarios. El 31 de
julio de 2026 pasó localmente con un MP4 de 103,810 bytes, 8.000 segundos,
360×640, 24 fps y H.264/AAC. También confirmó que resume no cambió el archivo ni
incrementó la invocación del renderer.

## Seguridad y limitaciones

La capa CLI/orquestación no importa ni ejecuta subprocess. La ejecución queda
restringida a los adaptadores controlados existentes, con `shell=False`. No hay
red, paid provider, API key, credencial, cloud, descarga, DaVinci ni MCP.
Ningún prompt entra en filenames o fingerprints de rutas.

El MVP prueba una cadena técnicamente completa, no calidad creativa final:
imágenes son sintéticas, narración es tonal y clips simulan movimiento local.
El siguiente paso recomendado es una interfaz local mínima. La activación de
providers reales debe ser una fase futura, separada y explícitamente autorizada.
