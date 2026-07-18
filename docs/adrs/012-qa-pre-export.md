# ADR 012: QA Pre-Export Obligatorio

## Status
Accepted

## Context
No podemos entregar clips de baja calidad o con errores técnicos.

## Decision
Todo clip debe pasar por `QAAgent` antes de ser marcado como exportado.
**Validaciones Sprint 1:**
- Resolución exacta: 1080x1920
- Codec de video: H.264
- Presencia de pista de audio
- Formato de contenedor: MP4

## Consequences
- **Positivas:** Calidad garantizada. Detección temprana de errores de renderizado.
- **Negativas:** Tiempo adicional de validación. Re-render si falla.

## Notes
En sprints futuros se agregarán: sincronización de subtítulos, validación de encuadre, métricas de calidad perceptual.
