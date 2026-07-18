# ADR 006: Model Registry and Capability Registry

## Status
Accepted

## Context
El sistema debe poder descubrir y administrar modelos disponibles sin depender de implementaciones concretas.

## Decision
Implementar un **Capability Registry** sobre un **Model Registry**:
- **Capability Registry:** El sistema depende de capacidades ("speech_recognition", "object_detection").
- **Model Registry:** Registra modelos concretos y sus adaptadores.
- Los agentes solicitan al Capability Registry: "Dame el provider por defecto para speech_recognition".

## Consequences
- **Positivas:** Desacoplamiento total. Un mismo agente puede usar Whisper hoy y WhisperX mañana sin cambios.
- **Negativas:** Complejidad adicional en bootstrap.

## Notes
Los modelos se descargan automáticamente a `~/.orion/models` en la primera ejecución.
