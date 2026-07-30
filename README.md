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
verificados. El provider simulado offline sigue siendo el default. Fase 5F.2 añade un adaptador
asíncrono OpenRouter con capabilities, gate de coste, remote jobs/polling durables y descarga
segura, pero permanece no ejecutable sin un publisher HTTPS real (no incluido). Consulta
[`docs/PRODUCTION_VIDEO_CLIP_GENERATION.md`](docs/PRODUCTION_VIDEO_CLIP_GENERATION.md) y
[`docs/PRODUCTION_OPENROUTER_VIDEO_PROVIDER.md`](docs/PRODUCTION_OPENROUTER_VIDEO_PROVIDER.md).

GENERATING_NARRATION consume el `ProductionScript` durable y crea un WAV PCM
determinista por escena, con manifest CAS, sidecars, checksum, recovery e
idempotencia. El audio es un placeholder audible, no una voz humana; el unico
provider es `simulated`, funciona sin red, FFmpeg, API key o cuenta financiada.
Consulta
[`docs/PRODUCTION_SIMULATED_SPEECH_GENERATION.md`](docs/PRODUCTION_SIMULATED_SPEECH_GENERATION.md).

La preparacion para TTS real agrega contratos neutrales de capabilities,
seleccion exacta de voz, precios Decimal, autorizacion facturable, fingerprints
y remote jobs durables. Sigue completamente deshabilitada: no existe adapter
real, API key, URL, discovery, red ni ruta ejecutable. Consulta
[`docs/PRODUCTION_REAL_TTS_PREPARATION.md`](docs/PRODUCTION_REAL_TTS_PREPARATION.md).

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
local renderer-neutral. Solo `dry_run` esta activo: persiste
`local-render-request.json` y `render-execution-manifest.json`, valida la
preparacion y no crea MP4 ni artefacto de video. `ffmpeg` y `davinci_resolve`
son identidades futuras deshabilitadas. Consulta
[`docs/PRODUCTION_LOCAL_RENDER_CONTRACT.md`](docs/PRODUCTION_LOCAL_RENDER_CONTRACT.md).

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
