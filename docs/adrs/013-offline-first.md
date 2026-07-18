# ADR 013: Offline-First Philosophy

## Status
Accepted

## Context
Orion AI debe funcionar como producto comercial sin depender de conectividad o APIs de terceros.

## Decision
Mantener filosofía **offline-first** en todo el desarrollo:
- Evitar LLMs para tareas resolubles con heurísticas, features y reglas deterministas.
- Reservar modelos generativos para funciones donde realmente aporten valor (comprensión narrativa profunda, contexto semántico avanzado).
- Toda IA es local por defecto. APIs remotas solo como opción futura.

## Consequences
- **Positivas:** Privacidad total. Sin costos de API. Funciona en cualquier lugar.
- **Negativas:** Requiere más esfuerzo de ingeniería para matchar calidad de APIs cloud.

## Notes
Esta decisión afecta todas las elecciones de modelos. Priorizamos modelos cuantizados, ONNX, y heurísticas robustas sobre llamadas a GPT-4/Claude.
