# ADR 005: Abstracción de Modelos IA

## Status
Accepted

## Context
Los modelos de IA (YOLO, Whisper, etc.) deben ser reemplazables sin modificar la lógica del sistema.

## Decision
Implementar el patrón Provider para cada capacidad de IA:
- `IObjectDetectionProvider`
- `ISpeechRecognitionProvider`
- `ISceneDetectionProvider`
- `IEmotionRecognitionProvider`
- `INarrativeModel`
- `IAttentionModel`

Cada agente depende de la interfaz provider, no del modelo concreto.

## Consequences
- **Positivas:** Reemplazo de modelos sin tocar agentes. Testing con dummy providers.
- **Negativas:** Capa adicional de abstracción por cada capacidad.

## Notes
Ningún módulo debe importar directamente `whisper`, `ultralytics`, etc. Siempre a través de adaptadores.
