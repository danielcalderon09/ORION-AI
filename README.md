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

# Soporte opcional OpenRouter para las cuatro etapas creativas durables
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

La infraestructura binaria interna almacena imágenes ya obtenidas por capacidades futuras bajo
`production/<job_id>/assets/images/`, con sidecar durable, MIME/extensión verificados, SHA-256 y
tamaño reales, escritura atómica, confinamiento del workspace y recovery sin reescritura. Esta
capacidad todavía no genera ni descarga imágenes y no invoca proveedores. Consulta
[`docs/PRODUCTION_BINARY_ASSETS.md`](docs/PRODUCTION_BINARY_ASSETS.md).

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Licencia
Proprietary - Orion AI Team
