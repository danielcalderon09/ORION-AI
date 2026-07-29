# Phase 5H.2: Durable Final Media Composition Planning

## Alcance

Phase 5H.2 convierte el stage existente `BUILDING_TIMELINE` en una etapa
durable que construye un plan completo, determinista y neutral respecto del
renderer. La etapa no genera contenido: sólo verifica y ordena video clips,
narración, música, efectos de sonido y, cuando ya existe, subtítulos.

No hay lógica de render, export, encoding, muxing ni escritura de frames. No se
añadieron FFmpeg, MoviePy, OpenTimelineIO, DaVinci Resolve, subprocess, HTTP,
SDKs, providers, API keys ni infraestructura cloud.

## Arquitectura

El bounded context vive en `backend/src/production/media_composition/`:

- `domain/`: contratos inmutables, fingerprints y construcción pura.
- `application/`: handler durable de `BUILDING_TIMELINE`.
- `ports.py`: lector, inventario, store y reloj.
- `infrastructure/`: inventario SQLAlchemy y lectura verificada de fuentes.
- `storage/`: plan write-once y manifest compare-and-swap.
- `recovery.py`: proyección determinista de validación.
- `reconciliation.py`: inspección read-only.
- `serialization.py`: JSON canónico y verificación de identidades.

El dominio no importa runtime, composition root, base de datos, transportes ni
renderers. Sólo infraestructura conoce los contratos upstream. El composition
root selecciona los adaptadores.

## Stage y compatibilidad

No se creó ni renombró un `ProductionStage`. Se reutiliza:

`generating_subtitles -> building_timeline -> rendering_long_form`

Los jobs existentes mantienen enum, orden y transiciones. El antiguo
`TimelineHandler` queda como fallback de factory; producción inyecta el handler
durable. Como el runtime pasa sólo los outputs de la etapa inmediatamente
anterior, el adapter consulta el inventario durable completo del job.

## Fuentes verificadas

Se selecciona la última revisión durable de:

- `ProductionScript`;
- `ProductionScenePlan`;
- `ProductionVideoClipManifest`;
- `SpeechGenerationManifest`;
- `AudioDesignManifest`.

Cada JSON exige registro READY, ruta contractual confinada, archivo regular sin
symlink/hard link, límite de lectura, UTF-8/JSON estricto, schema soportado,
tamaño exacto y SHA-256 coincidente. Los manifests deben estar completos y sus
entries `stored`.

`AudioDesignPlan` se deriva nuevamente desde el script para recuperar offsets
SFX y ducking; su fingerprint debe coincidir con el manifest. Cada shot exige
exactamente un video clip `primary`.

## Subtítulos

El baseline actual sólo produce un artifact simulado vacío. Phase 5H.2 no
infiere texto: crea `track-subtitles` deshabilitado con
`no_durable_subtitle_asset`.

Si existe un SRT durable no vacío, se validan UTF-8, numeración, timestamps,
orden, texto, overlaps y límites. El plan guarda la referencia al SRT, índice
de cue, rango, ubicación `bottom_center` y hash de texto; no duplica el texto.

## Timeline y tracks

| Orden | Track | Contenido |
|---:|---|---|
| 0 | `track-video` | un clip primario por shot |
| 1 | `track-narration` | un WAV por escena |
| 2 | `track-music` | cero o un bed solicitado |
| 3 | `track-sound-effects` | sólo cues explícitos |
| 4 | `track-subtitles` | SRT durable o pista deshabilitada |

Cada clip contiene orden, scene/shot, asset, start/end en frames y
milisegundos, rango fuente, fades y volume envelope. Los frames usan enteros:

`frame = floor((milliseconds * fps + 500) / 1000)`

Video empieza en frame cero, es contiguo y termina en
`expected_duration_frames`. Resolución, FPS y aspect ratio derivan de clips
uniformes. Pixel aspect es `1:1`, color space `rec709`, title safe 80% y action
safe 90%.

Si un video clip durable es más corto que el slot aprobado del shot, el plan
no oculta el mismatch: fija `playback_mode=loop`, `loop_count` entero y
`source_out_frame`, y registra `source_duration_looped` como warning. No crea
frames nuevos; la repetición queda como instrucción explícita para el renderer.

Las transiciones preservan `none`, `cut`, `dissolve`, `fade`, `wipe` y
`match_cut`. `dissolve` representa un crossfade futuro, sin solapar ni procesar
clips ahora. Narración usa 0 dB; música -18 dB, fades de 250 ms y ducking a -30
dB; SFX -6 dB. Son instrucciones, no audio procesado.

## Almacenamiento

```text
production/{job_id}/building_timeline/attempt-{n}/
  media-composition-plan.json
  media-composition-manifest.json
```

Los eventos operacionales quedan en el runtime durable; no se duplica un log
con paths o excepciones. El manifest contiene sólo metadata segura de recovery.

Ambos JSON usan UTF-8, newline final, claves ordenadas, rechazo de duplicados,
NaN e Infinity, límites, workspace confinement, rechazo de links, lock,
temporary file local, `fsync`, replace atómico y directory fsync donde el SO lo
permite.

## Plan y fingerprints

Schema/plan version: `1.0.0`. `media-composition-plan.json` contiene manifests
fuente; inventario ordenado con paths relativos, MIME, SHA-256, tamaños,
fingerprints y metadata media; perfil de salida; cinco tracks; clips;
transiciones; ducking; envelopes; subtítulos y duración esperada.

No contiene timestamps, attempt, bytes, paths absolutos, secrets, URLs,
requests ni comandos.

- `source_fingerprint`: inventario y manifests.
- `timeline_checksum`: output, tracks, clips, orden, frames, transiciones,
  subtítulos, ducking y envelopes.
- `plan_fingerprint`: contrato completo excepto su propio campo.

Todos usan JSON canónico y SHA-256 y se recalculan al leer. Nunca dependen del
momento de ejecución.

## Manifest

`media-composition-manifest.json` schema `1.0.0` contiene versión, timestamps
timezone-aware, fingerprints, referencia/checksum/tamaño del plan, inventario
por asset, validation summary, issues y estado:

- `prepared`;
- `complete`;
- `invalidated`;
- `failed`.

El summary contabiliza gaps, overlaps, missing, corrupt, duplicate y orphan
assets, duration/frame mismatches, errores y warnings. `complete` exige todos
los assets disponibles.

## Validaciones

Se falla cerrado ante gaps/overlaps, assets ausentes/corruptos/duplicados,
shots sin clip o con múltiples primarios, duraciones negativas o fuera del
timeline, frame count/FPS incompatibles, resolución/FPS inconsistentes, música
que no cubre el timeline, narración/SFX fuera de límites, subtítulos inválidos,
transiciones imposibles, paths/checksums incompatibles, schemas no soportados y
fingerprints alterados. Orphans son warnings y nunca se eliminan.

## Recovery e idempotencia

- Plan inexistente: construir y escribir una vez.
- Plan válido: reutilizar bytes exactos.
- Identidad distinta: conflicto fail-closed.
- Manifest ausente: reconstruirlo desde el plan verificado.
- Reentrega idéntica: no cambiar plan ni manifest.
- Concurrencia: lock/CAS; el perdedor reintenta.
- Asset desaparecido: conservar el plan e invalidar sólo su entrada.
- Asset restaurado y verificado: marcar sólo esa entrada disponible.
- Corrupción: no sobrescribir ni adoptar.
- Cancelación: propagar `CancelledError`.

`MediaCompositionReconciler` sólo lee y reporta presencia, validez,
fingerprints, expected/available/missing/corrupt/orphan assets, recovery seguro,
intervención manual y completitud. No genera, repara, elimina ni renderiza.

## Configuración y seguridad

Los únicos settings nuevos son límites:

```text
ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES=4000000
ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES=4000000
ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES=4000000
```

No existe setting de provider, URL, API key, executable o cloud. Guardas AST
prohíben HTTP, subprocess, FFmpeg, MoviePy, OpenTimelineIO, DaVinci y SDKs.

## Limitaciones

- Aún no hay SRT durable por defecto.
- Transiciones, fades y envelopes son instrucciones.
- No hay normalización, mezcla, render, encoding, muxing ni export.
- No se crean MP4, MOV ni frames.
- `RENDERING_LONG_FORM` sigue fuera de esta fase.

Un renderer futuro podrá leer sólo este plan, abrir los assets relativos
referenciados y verificar sus checksums para producir el video completo.
