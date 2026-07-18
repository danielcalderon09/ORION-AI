# ADR 002: Clean Architecture con DDD

## Status
Accepted

## Context
Orion AI debe escalar durante años sin deuda técnica. Se requiere que cualquier módulo (IA, edición, exportación) sea reemplazable sin efectos colaterales.

## Decision
Aplicar Arquitectura Limpia (Clean Architecture) con Domain-Driven Design:
- **Capa Domain (Core):** Entidades, value objects, eventos de dominio, interfaces de repositorio. Sin dependencias externas.
- **Capa Application:** Casos de uso, DTOs, puertos de entrada/salida. Depende solo de Domain.
- **Capa Agents / Cognition / Production:** Implementaciones de capacidades. Depende de Application.
- **Capa Infrastructure:** Adaptadores concretos (FFmpeg, SQLite, ONNX, etc.). Depende de las capas superiores a través de interfaces.

## Consequences
- **Positivas:** Testabilidad absoluta. Reemplazo de modelos sin tocar lógica de negocio. Separación clara de responsabilidades.
- **Negativas:** Mayor cantidad de archivos y abstracciones iniciales. Curva de aprendizaje para nuevos desarrolladores.

## Notes
Las flechas de dependencia apuntan siempre hacia adentro. Infrastructure conoce a Domain, pero Domain no conoce a Infrastructure.
