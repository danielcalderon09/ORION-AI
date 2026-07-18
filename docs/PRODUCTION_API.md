# Production Jobs API interna (Fase 4)

## Alcance y activación

La API se registra únicamente con `ORION_PROMPT_VIDEO_ENABLED=True`. Su valor predeterminado
continúa siendo `False`; así no existen rutas, container, engine, conexión ni task de
Production. Todas las rutas usan el prefijo histórico `/api/v1`:

- `POST /api/v1/production/jobs`
- `GET /api/v1/production/jobs`
- `GET /api/v1/production/jobs/{job_id}`
- `POST /api/v1/production/jobs/{job_id}/cancel`
- `POST /api/v1/production/jobs/{job_id}/retry`
- `GET /api/v1/production/jobs/{job_id}/events`
- `GET /api/v1/production/jobs/{job_id}/artifacts`

Los controllers validan HTTP, llaman casos de uso y traducen errores. No importan
SQLAlchemy ni alteran estados directamente.

## Creación e idempotencia

`CreateProductionJobRequest` acepta prompt normalizado (máximo 10 000 caracteres),
configuración, opción de clips, `client_request_id` y metadata. Rechaza campos extra,
claves sensibles y paths absolutos. El orquestador realiza `CREATED -> QUEUED`; el worker
crea posteriormente el primer comando.

Cuando existe `client_request_id`, un SHA-256 cubre JSON canónico de prompt, configuración,
metadata y opción de clips. Mismo ID y contenido devuelve el mismo job; contenido distinto
responde `409`. Esta identidad no reutiliza la idempotencia de `StageCommand`.

## Consultas y operaciones

La lista se ordena por `created_at DESC, job_id DESC`, admite filtros `status`/`stage`,
offset no negativo y limit 50 (máximo 100). Eventos se ordenan por secuencia y artefactos
por ruta/ID. Las rutas permanecen relativas.

Cancelación es durable e idempotente para `CANCEL_REQUESTED`/`CANCELLED`; el worker termina
una etapa activa antes de cancelar. Retry manual admite `FAILED` y `NEEDS_USER_ACTION`,
limpia errores, conserva audit log y vuelve a `QUEUED`. Optimistic locking resuelve carreras.

## Errores y seguridad

- `400`: validación de aplicación.
- `404`: job inexistente.
- `409`: estado, idempotencia o concurrencia incompatible.
- `422`: contrato HTTP inválido.
- `503`: runtime o esquema no disponible.

El envelope sigue FastAPI: `detail.code` y `detail.message`. No devuelve SQL, tracebacks,
paths locales o secretos. Sanitización recursiva protege `api_key`, `token`, `secret`,
`password` y `credential`. Los prompts completos no deben registrarse.

## Composition root, lifecycle y esquema

`ProductionContainer` es inmutable y vive en `app.state` durante el lifespan. Startup
valida Alembic, ejecuta recovery e inicia una task; shutdown activa `stop_event`, espera con
timeout, cancela cooperativamente si hace falta y dispone el engine. No hay tasks al importar.

`ORION_PRODUCTION_AUTO_MIGRATE=False` exige revisión `20260718_0003`. Si se habilita,
`upgrade head` usa exclusivamente la URL resuelta. Runtime real nunca sustituye Alembic con
`metadata.create_all()`. Polling, lease, heartbeat, owner y timeout son configurables;
heartbeat debe ser menor que lease.

Esta fase no incluye frontend, WebSockets/SSE, providers, multimedia, DaVinci, FFmpeg,
OpenCV, MCP, EventBus ni ejecución distribuida. Los handlers siguen simulados.
