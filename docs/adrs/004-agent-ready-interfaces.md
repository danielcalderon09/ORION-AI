# ADR 004: Agent-Ready Architecture

## Status
Accepted

## Context
Orion AI evolucionará desde engines estáticos hacia agentes autónomos con memoria, planificación y uso de herramientas.

## Decision
Cada capacidad del sistema (visión, audio, dirección, QA) se implementa como un módulo bajo `agents/` o `production/` con interfaz base `IAgent`. Cada agente:
- Recibe `AgentInput` inmutable.
- Retorna `AgentResult` inmutable.
- Expone sus capacidades vía `get_capabilities()`.

## Consequences
- **Positivas:** Preparación futura para agentes sin reescritura. Interoperabilidad garantizada por contrato.
- **Negativas:** Overhead de abstracción inicial. Algunos módulos simples parecerán over-engineered en Sprint 1.

## Notes
En Sprint 1 los "agentes" son stateless. La infraestructura de memoria y planificación se agregará en sprints futuros sin romper contratos.
