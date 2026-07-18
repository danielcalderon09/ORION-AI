# ADR 011: Heurística Narrativa Sprint 1

## Status
Accepted

## Context
El Sprint 1 requiere comprensión narrativa básica sin depender de LLMs multimodales.

## Decision
Implementar `HeuristicNarrativeProvider` para el Narrative Intelligence Agent:
- **Beats narrativos:** Detectados por cambios bruscos de escena (OpenCV histogram comparison) + picos de audio energy.
- **Estructura:** Introducción (primer 10%), Desarrollo (medio), Clímax (pico de atención + audio), Resolución (último 15%).
- **Micro-historias:** Segmentos entre dos picos de atención consecutivos.

## Consequences
- **Positivas:** 100% offline. Rápido. No requiere GPU pesada.
- **Negativas:** Precisión limitada comparada con modelos multimodales futuros.

## Notes
Las interfaces `INarrativeModel` están diseñadas para que en Sprint 2+ se inyecte un provider basado en LLM sin modificar el agente.
