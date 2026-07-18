# ADR 001: Lenguaje y Framework Principal

## Status
Accepted

## Context
Orion AI es una plataforma de comprensión de video asistida por IA que requiere procesamiento intensivo de multimedia, inferencia de modelos de machine learning y una interfaz de usuario desktop moderna.

## Decision
- **Backend:** Python 3.11+ para todo el procesamiento de video, IA y lógica de negocio.
- **Frontend:** TypeScript + React dentro de Electron para la shell desktop.
- **Comunicación:** HTTP REST (FastAPI) + WebSocket (Socket.IO) exclusivamente.

## Consequences
- **Positivas:** El ecosistema Python domina IA y multimedia. React/Electron permite UI moderna sin mezclar lógica de IA en la capa de presentación.
- **Negativas:** Se requiere coordinar dos procesos (Python backend + Electron frontend). El backend debe gestionar su propio ciclo de vida.

## Notes
Electron será responsable de iniciar y detener automáticamente el backend Python. El usuario nunca ejecuta procesos manualmente.
