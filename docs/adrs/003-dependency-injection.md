# ADR 003: Dependency Injection

## Status
Accepted

## Context
Para mantener el desacoplamiento entre capas y permitir testing con mocks, necesitamos un mecanismo de inyección de dependencias.

## Decision
Usar `dependency-injector` como framework de DI. Configuración declarativa en `infrastructure/di/container.py`. Todos los adaptadores (FFmpeg, modelos, DB) se registran como providers.

## Consequences
- **Positivas:** Fácil testing unitario con mocks. Cambio de implementaciones sin modificar lógica de negocio. Ciclo de vida de objetos centralizado.
- **Negativas:** Dependency injection puede ocultar dependencias si no se documenta bien.

## Notes
Ningún módulo debe crear instancias concretas de adaptadores. Siempre solicitar al container.
