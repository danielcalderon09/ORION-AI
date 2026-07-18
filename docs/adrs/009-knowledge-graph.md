# ADR 009: Knowledge Graph MVP

## Status
Accepted

## Context
La comprensión de un video requiere representar relaciones semánticas, no solo datos tabulares.

## Decision
- **MVP:** NetworkX en memoria con serialización a JSONL/GraphML.
- **Persistencia:** `knowledge/knowledge_graph.jsonl` dentro del workspace.
- **Futuro:** Migración a Neo4j o KùzuDB cuando el grafo exceda memoria RAM.

## Consequences
- **Positivas:** Rápido de iterar. Sin dependencias pesadas. Consultas complejas de relaciones.
- **Negativas:** Límite de memoria para videos muy largos. Mitigado por chunking narrativo.

## Notes
El Knowledge Graph representa: eventos, objetos, personajes, escenas, relaciones, acciones, emociones, narrativa.
