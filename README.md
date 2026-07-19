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

# Soporte opcional del proveedor OpenAI de PLANNING
pip install -e ".[planning-openai]"
```

La instalación base conserva `SimulatedPlanningProvider` y no necesita `httpx`. El extra
`planning-openai` habilita el único adaptador HTTP real. Los perfiles pueden verificarse sin
llamadas externas con `python scripts/verify_planning_install_profiles.py`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Licencia
Proprietary - Orion AI Team
