# ORION AI

**AI Powered Intelligent Video Understanding Platform**

Orion AI comprende completamente un video, encuentra automáticamente los mejores momentos y genera contenido listo para publicarse en TikTok, YouTube Shorts y Facebook Reels.

## Arquitectura

Orion AI sigue una arquitectura de comprensión narrativa (Narrative Intelligence) con las siguientes capas:

- **Core:** Dominio puro, casos de uso, Project Brain
- **Perception (Agents):** Visión, Audio, Speech, OCR, Emoción, Narrativa, Atención
- **Cognition:** Video Understanding, Knowledge Graph, Context Engine
- **Production:** Director AI, Director of Photography AI, Creative Brief
- **Viral Intelligence:** Viral Score Engine, Hook Optimizer, Retention Simulator, Audience Director
- **Auto-Improvement:** Reflection Engine, Critic AI, Multi-Candidate Generator, Consensus Engine, Creative Memory, Human Feedback
- **Production Hardening:** Performance Profiler, Memory Manager, Checkpoints, Pipeline Cache, Config Profiles, Observability, Versioning
- **Infrastructure:** FFmpeg, SQLite, GPU Manager, Telemetry, Benchmark Suite

> **Architecture Frozen**: Sprint 5 approved. No new architectural layers without ADR. See [`docs/VALIDATION_ROADMAP.md`](docs/VALIDATION_ROADMAP.md).

## Estructura del Repositorio

```
orion-ai/
├── backend/         # Python - Clean Architecture
├── frontend/        # Electron + React
├── docs/            # ADRs + Validation Roadmap
├── models/          # Pesos descargados (~/.orion/models)
└── scripts/         # CI/CD, profiling, batch processing
```

## CI/CD / Validación

```bash
# Local quality gate (same as CI)
python scripts/lint_check.py

# Run all tests with coverage
python scripts/run_all_tests.py --coverage

# Generate profiling report
python scripts/profile_pipeline.py --simulate --format=all

# Batch process a folder of videos
python scripts/batch_processor.py --videos=./samples --profile=balanced --max-workers=2
```

### Requisitos
- Python 3.11+
- Node.js 20+
- FFmpeg (incluido en bundle o instalado en sistema)

### Backend
```bash
pip install -e ".[dev]"
python -m backend.src.main

# Soporte opcional OpenRouter para etapas creativas y adquisición de imágenes
pip install -e ".[production-llm]"

# Alias descriptivo equivalente
pip install -e ".[production-openrouter]"
```

La instalación base conserva `SimulatedPlanningProvider` y no necesita `httpx`. El extra
`production-llm` habilita el transporte OpenAI-compatible usado por OpenRouter. Los extras
`planning-openai` y `production-openai` permanecen solo por compatibilidad de instalación.
Los perfiles pueden verificarse sin
llamadas externas con `python scripts/verify_planning_install_profiles.py`.

SCRIPTING consume el plan durable, verifica tamano/SHA-256 y escribe
`production/<job_id>/scripting/attempt-<n>/production-script.json`. Su provider default es
`simulated`; el provider real principal es OpenRouter lazy, con modelos configurables de
OpenAI, Anthropic, Google, DeepSeek, Qwen y otros disponibles en OpenRouter. Consulta
[`docs/PRODUCTION_SCRIPTING_PROVIDER.md`](docs/PRODUCTION_SCRIPTING_PROVIDER.md).

SCENE_PLANNING consume exclusivamente el `ProductionScript` durable y produce
`production/<job_id>/scene_planning/attempt-<n>/scene-plan.json`. Su provider predeterminado
tambien es `simulated`; OpenRouter se habilita de forma independiente y lazy. El artifact contiene
escenas, shots, camara, timing y transiciones tipados, sin generar imagenes ni video. Consulta
[`docs/PRODUCTION_SCENE_PLANNING_PROVIDER.md`](docs/PRODUCTION_SCENE_PLANNING_PROVIDER.md).

VISUAL_ASSET_PLANNING consume exclusivamente el `ProductionScenePlan` durable y produce
`production/<job_id>/visual_asset_planning/attempt-<n>/visual-asset-plan.json`. El plan contiene
especificaciones tipadas por shot, cámara aprobada, timing, dimensiones, prompts seguros y una
biblia de continuidad; no genera ni descarga archivos multimedia. `simulated` sigue siendo el
default y OpenRouter es opcional/lazy. Consulta
[`docs/PRODUCTION_VISUAL_ASSET_PLANNING_PROVIDER.md`](docs/PRODUCTION_VISUAL_ASSET_PLANNING_PROVIDER.md).

La infraestructura binaria interna almacena imágenes verificadas bajo
`production/<job_id>/assets/images/`, con sidecar durable, MIME/extensión verificados, SHA-256 y
tamaño reales, escritura atómica, confinamiento del workspace y recovery sin reescritura. Consulta
[`docs/PRODUCTION_BINARY_ASSETS.md`](docs/PRODUCTION_BINARY_ASSETS.md).

ACQUIRING_ASSETS consume el plan visual durable, almacena imágenes mediante esa infraestructura
binaria y publica `image-acquisition-manifest.json`. El provider default es simulado, offline y
determinista; OpenRouter Images API es opcional y no permite fallback. Consulta
[`docs/PRODUCTION_IMAGE_ACQUISITION_PROVIDER.md`](docs/PRODUCTION_IMAGE_ACQUISITION_PROVIDER.md).

GENERATING_VIDEO_CLIPS consume exclusivamente ese manifest completed y sus `SOURCE_IMAGE`
verificados. El provider simulado offline sigue siendo el default. Fase 6F.1 integra Veo 3.1 Lite
image-to-video con checkpoint previo al submit, una sola submission facturable, polling/recovery
durables, publicación del primer frame mediante el boundary existente y descarga MP4 acotada.
La URL configurada debe servir realmente el publication root por HTTPS. Consulta
[`docs/PRODUCTION_VIDEO_CLIP_GENERATION.md`](docs/PRODUCTION_VIDEO_CLIP_GENERATION.md) y
[`docs/PRODUCTION_OPENROUTER_VIDEO_PROVIDER.md`](docs/PRODUCTION_OPENROUTER_VIDEO_PROVIDER.md).

GENERATING_NARRATION consume el `ProductionScript` durable y crea un WAV PCM
por escena, con manifest CAS, sidecars, checksum, recovery e idempotencia. El
default sigue siendo `simulated`; Phase 6D agrega Kokoro sobre OpenRouter como
provider real opt-in, con coste explícito y un request durable por segmento.

La implementación de imagen y TTS reales, el mapa fijo de modelos, recovery,
límites facturables y activación manual están documentados en
[`docs/PRODUCTION_OPENROUTER_MEDIA_PROVIDERS.md`](docs/PRODUCTION_OPENROUTER_MEDIA_PROVIDERS.md).

La evaluacion oficial y fechada de TTS real no selecciona todavia un provider:
Azure queda como primer candidato de listening test y Google como segundo,
pendientes de calidad auditiva, precio Azure verificable y revision legal. No
se implemento integracion ni se activo gasto. Consulta
[`docs/PRODUCTION_TTS_PROVIDER_EVALUATION.md`](docs/PRODUCTION_TTS_PROVIDER_EVALUATION.md)
y [`docs/adrs/019-real-tts-provider-selection.md`](docs/adrs/019-real-tts-provider-selection.md).

La preparacion del listening test define ocho muestras publicas, tres slots
bloqueados, 24 unidades no autorizadas, normalizacion comun, blinding HMAC,
scorecards, umbrales criticos y un techo futuro de USD 10 que no autoriza
gasto. No genera audio ni ejecuta el test. Consulta
[`docs/PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md`](docs/PRODUCTION_TTS_LISTENING_TEST_PREPARATION.md)
y [`docs/PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md`](docs/PRODUCTION_TTS_LISTENING_TEST_RUNBOOK.md).

PREPARING_MUSIC ahora deriva solo metadata explicita de audio design del
`ProductionScript`, genera beds musicales y cues SFX sinteticos como WAV PCM
deterministas, y los checkpointa con stores separados, manifest CAS, recovery
y reconciliacion read-only. Si no hay metadata explicita, completa con cero
assets. Solo existen providers `simulated`; no hay samples externos, red,
FFmpeg, DaVinci, mezcla final ni render final. Consulta
[`docs/PRODUCTION_SIMULATED_AUDIO_DESIGN.md`](docs/PRODUCTION_SIMULATED_AUDIO_DESIGN.md).

BUILDING_TIMELINE ahora construye `media-composition-plan.json` y
`media-composition-manifest.json` durables desde assets ya verificados. El plan
ordena video, narracion, musica, SFX y subtitulos con frames, transiciones,
fades, ducking, safe areas y fingerprints. No genera contenido ni ejecuta
render, FFmpeg, MoviePy, OpenTimelineIO o DaVinci. Consulta
[`docs/PHASE_5H2_MEDIA_COMPOSITION_PLAN.md`](docs/PHASE_5H2_MEDIA_COMPOSITION_PLAN.md).

RENDERING_LONG_FORM consume ese plan y manifest verificados mediante un contrato
local renderer-neutral. `dry_run` conserva la validacion sin media y `ffmpeg`
puede activarse explicitamente para producir un MP4 real, validarlo con FFprobe
y registrar `LONG_FORM_RENDER`. DaVinci permanece deshabilitado. Consulta
[`docs/PRODUCTION_LOCAL_RENDER_CONTRACT.md`](docs/PRODUCTION_LOCAL_RENDER_CONTRACT.md)
y
[`docs/PRODUCTION_LOCAL_FFMPEG_RENDERER.md`](docs/PRODUCTION_LOCAL_FFMPEG_RENDERER.md).

VALIDATING_RENDER vuelve a comprobar de forma independiente el MP4 y toda su
cadena durable con FFprobe, sin ejecutar FFmpeg ni modificar el render. Solo
despues emite `FINAL_RENDER_VALIDATION` y el pipeline puede aceptar el trabajo
como completado. Consulta
[`docs/PRODUCTION_FINAL_RENDER_VALIDATION.md`](docs/PRODUCTION_FINAL_RENDER_VALIDATION.md).

La Fase 6A conecta el pipeline completo mediante el modo explícito
`local_simulated_e2e`. Un CLI crea o reanuda un job durable, recorre las 12
etapas canónicas, usa providers creativos simulados, renderiza un MP4 real con
FFmpeg y solo termina tras `FINAL_RENDER_VALIDATION`. No requiere red, API key
ni provider de pago. El contenido es una prueba técnica, no calidad creativa
final. Consulta
[`docs/PRODUCTION_END_TO_END_LOCAL_MVP.md`](docs/PRODUCTION_END_TO_END_LOCAL_MVP.md).

```text
python -m backend.src.production.cli.generate_video --prompt "Explica en un video corto tres curiosidades sobre Marte." --mode local_simulated_e2e
```

El primer cliente gráfico nativo reutiliza exactamente ese backend mediante
PySide6. Muestra trabajos durables, progreso real por etapa, resume y el MP4
validado sin ejecutar FFmpeg ni modificar manifests desde la interfaz. Consulta
[`docs/ORION_DESKTOP_UI.md`](docs/ORION_DESKTOP_UI.md).

SCRIPTING admite ahora el provider cerrado `openrouter` como opt-in controlado,
manteniendo `simulated` como default. La integración usa salida JSON Schema
estricta, checkpoint durable previo a cualquier solicitud, coste Decimal y una
regla `uncertain` que impide reenvíos automáticos. La configuración comprometida
no contiene key, modelo ni autorización de gasto; esta fase se verificó
exclusivamente con transports falsos. Consulta
[`docs/PRODUCTION_OPENROUTER_SCRIPTING.md`](docs/PRODUCTION_OPENROUTER_SCRIPTING.md).

La evaluación pública y fechada de modelos de scripting propone, sin activar,
`google/gemini-2.5-flash-lite` como candidato económico y
`openai/gpt-4.1-mini` como fallback de calidad. `simulated` continúa activo, el
modelo default permanece vacío y no se usaron key, cuenta, generación ni
créditos. Consulta
[`docs/PRODUCTION_OPENROUTER_SCRIPTING_MODEL_SELECTION.md`](docs/PRODUCTION_OPENROUTER_SCRIPTING_MODEL_SELECTION.md)
y [`ADR-020`](docs/adrs/020-openrouter-scripting-model-selection.md).

```text
pip install -e ".[desktop]"
python -m backend.src.desktop
```

La infraestructura de publicacion segura es un bounded context independiente y desactivado por
defecto. Publica temporalmente solo bytes ya verificados, con manifiesto durable, expiracion,
recovery, cleanup y reconciliacion de solo lectura. Incluye adaptadores `null` y filesystem de
desarrollo, pero ningun cloud publisher ni servidor publico. Consulta
[`docs/PRODUCTION_ASSET_PUBLISHING.md`](docs/PRODUCTION_ASSET_PUBLISHING.md).

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Licencia
Proprietary - Orion AI Team
