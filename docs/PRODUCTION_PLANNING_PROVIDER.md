# Proveedor intercambiable de PLANNING

PLANNING produce `production-plan.json` mediante un contrato independiente del proveedor.
`simulated` es el valor predeterminado, determinista y sin red. El proveedor real principal es
`openrouter`, cargado de forma lazy y apoyado en `OpenRouterPlanningProvider`.

## Transporte y modelos

Planning y Scripting comparten `OpenAICompatibleResponsesClient`, una infraestructura neutral
para APIs OpenAI-compatible. OpenRouter usa `POST /chat/completions`, mensajes system/user y
Structured Outputs (`response_format=json_schema`). La petición exige
`provider.require_parameters=true`; si el modelo o la ruta elegida no admite el schema, el error
es explícito y no existe fallback a texto libre ni reparación con regex. También se envía
`provider.data_collection=deny`.

El modelo es configurable con su identificador completo de OpenRouter, por ejemplo un slug con
prefijo `openai/`, `anthropic/`, `google/`, `deepseek/` o `qwen/`. El sistema no consulta el
catálogo durante startup ni selecciona modelos por precio. `requested_model` y
`reported_model` se conservan por separado; el segundo puede faltar y un mismatch queda en la
metadata del artefacto.

## Settings

- `ORION_PLANNING_PROVIDER=simulated` o `openrouter`.
- `ORION_PLANNING_MODEL=openai/gpt-4.1-mini` (default sustituible).
- `ORION_PLANNING_API_KEY` (obligatoria solo con `openrouter`).
- `ORION_PLANNING_BASE_URL=https://openrouter.ai/api/v1`.
- `ORION_PLANNING_TIMEOUT_SECONDS=30`.
- `ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS=2` (1..5).
- `ORION_PLANNING_RETRY_BASE_DELAY_SECONDS=0.25`.
- `ORION_PLANNING_MAX_OUTPUT_TOKENS=4096`.
- `ORION_PLANNING_TEMPERATURE=0.2`.
- `ORION_OPENROUTER_HTTP_REFERER` (opcional, global).
- `ORION_OPENROUTER_APP_TITLE` (opcional, global; se envía como `X-Title`).

Provider desconocido, modelo vacío, key ausente o URL no HTTPS/sin host/con credenciales
embebidas hacen fallar startup sin fallback. Los headers opcionales aceptan únicamente valores
globales validados; nunca se aceptan por job ni se escriben en DB, logs, artifacts o metadata.
Authorization también permanece fuera de todos los contratos públicos.

## Retry, errores y lifecycle

Timeout, conexión, HTTP 429 y 5xx son reintentables con backoff e intentos acotados. HTTP
401/403 requiere intervención y 4xx restantes o respuestas inválidas no se reintentan. El retry
durable sigue perteneciendo al orquestador. `CancelledError` se propaga. El cliente async se
cierra durante shutdown; importar backend o usar el feature apagado no importa `httpx`, no crea
cliente y no crea tasks.

## Artefacto y seguridad

El writer publica JSON canónico UTF-8 mediante temporal y replace atómico en
`production/<job_id>/planning/attempt-<n>/production-plan.json`. Rechaza traversal y symlinks;
checksum SHA-256 y tamaño se calculan sobre los bytes reales. La API lista metadata segura, no
el contenido del plan ni prompts/respuestas externos.

## Instalación y rollback

La instalación mínima es `pip install -e .`. El perfil recomendado es
`pip install -e ".[production-llm]"`; `production-openrouter` es un alias descriptivo.
`planning-openai` y `production-openai` se conservan solo como extras de instalación compatibles,
no como provider recomendado ni como selector de runtime. Para rollback use
`ORION_PLANNING_PROVIDER=simulated` y reinicie.

Las pruebas usan exclusivamente `httpx.MockTransport`; no hacen llamadas reales ni facturables.
