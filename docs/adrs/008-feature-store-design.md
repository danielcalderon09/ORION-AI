# ADR 008: Feature Store

## Status
Accepted

## Context
Los resultados de cada agente (features) deben ser reutilizables, versionados y accesibles sin reprocesar.

## Decision
Implementar Feature Store híbrido:
- **Storage:** Filesystem local (Parquet para datos tabulares, NPZ para arrays, JSON para metadatos).
- **Registry:** SQLite para indexar qué features existen, su versión, y su ubicación.
- **Ubicación:** Dentro del workspace autocontenido del proyecto (`features/`).

## Consequences
- **Positivas:** Zero reprocesamiento. Fácil debugging. Exportable para training futuro.
- **Negativas:** I/O de disco puede ser lento para features masivos. Mitigado por lazy loading.

## Notes
Todo agente DEBE guardar sus resultados en el Feature Store antes de retornar.
