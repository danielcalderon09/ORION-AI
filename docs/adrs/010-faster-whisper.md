# ADR 010: Faster Whisper over OpenAI Whisper

## Status
Accepted

## Context
Necesitamos transcripción de audio estable, rápida y con bajo uso de memoria.

## Decision
Usar `faster-whisper` con modelo `base` como implementación por defecto del `ISpeechRecognitionProvider`.

**Razones:**
- Más rápido que Whisper original
- Usa significativamente menos memoria
- Excelente precisión para MVP
- Muy estable y mantenido

## Consequences
- **Positivas:** Mejor rendimiento en hardware modesto. Compatible con CPU y GPU.
- **Negativas:** Dependencia adicional (`ctranslate2`). Modelo debe descargarse en primera ejecución.

## Notes
El modelo se almacena en `~/.orion/models/whisper-base`. Nunca en el repositorio.
