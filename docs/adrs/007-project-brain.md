# ADR 007: Project Brain

## Status
Accepted

## Context
Cada proyecto de video genera una enorme cantidad de datos: features, decisiones del Director AI, exportaciones, preferencias. Necesitamos centralizar la memoria del proyecto.

## Decision
Crear una entidad de dominio **Project Brain** que centralice toda la memoria de un proyecto:
- Índice de features disponibles
- Narrative memory (decisiones de Narrative Intelligence Agent)
- Director memory (decisiones del Director AI)
- Historial de exportaciones
- Preferencias del usuario
- Validaciones de QA

## Consequences
- **Positivas:** Cualquier módulo consulta un solo punto de verdad. Reapertura de proyectos sin reprocesamiento.
- **Negativas:** El Project Brain puede crecer grande; requiere diseño cuidadoso de lazy loading.

## Notes
Project Brain se serializa en `brain/project_brain.json` dentro del workspace del proyecto.
