# Infraestructura durable de assets binarios

Esta infraestructura almacena imágenes de Production sin conocer su proveedor. Fase 5E.2 conecta
ACQUIRING_ASSETS: el provider entrega bytes a `BinaryAssetWriter` y recibe un
`ProductionBinaryAsset` durable. El store sigue sin importar OpenRouter, HTTP ni contratos de
generación.

## Contratos y ruta

`ProductionBinaryAsset` conserva identidad de job, escena y shot, rol, MIME, extensión, SHA-256,
tamaño, dimensiones, creación UTC, ruta relativa y `ProductionBinaryAssetMetadata` segura.
`ProductionBinaryAssetReference` contiene la expectativa mínima para una lectura verificada.
Todos los modelos son estrictos e inmutables.

La única ruta admitida para imágenes es:

```text
production/<job_id>/assets/images/<asset-id>.<ext>
```

Junto al archivo se publica un sidecar canónico:

```text
production/<job_id>/assets/images/<asset-id>.<ext>.asset.json
```

El sidecar permite resolver metadata durable y detectar tanto archivo sin metadata como metadata
sin archivo sin depender de un proveedor. No contiene rutas absolutas, credenciales, headers ni
payloads externos.

## Escritura, lectura y recovery

`FilesystemBinaryAssetStore` implementa `BinaryAssetStore`, `BinaryAssetWriter` y
`BinaryAssetReader`. La escritura valida bytes antes de tocar disco, crea un temporal en el mismo
directorio, hace `flush`, `fsync` y `os.replace`, y calcula SHA-256 y tamaño sobre los bytes
reales. PNG, JPEG y WebP son los formatos iniciales. La extensión debe corresponder al MIME y
Pillow debe poder verificar y decodificar por completo la imagen.

Una lectura vuelve a comprobar archivo regular, límite de tamaño, tamaño exacto, SHA-256, MIME,
extensión y dimensiones. WorkspaceConfinement rechaza rutas absolutas, traversal, escapes,
symlinks, junctions y hard links. Nunca se confía solo en metadata.

Si binario y sidecar existentes coinciden exactamente con la solicitud y superan toda la
verificación, el store devuelve la metadata existente sin reescribir. Una pareja incompleta,
corrupta o incompatible produce error tipado y conserva los bytes para diagnóstico.

## Configuración e inyección

`AssetStorageConfiguration` recibe internamente workspace, tamaño máximo, MIME permitidos y
extensiones permitidas. El container la construye desde `PROJECTS_DIR` y:

```text
ORION_BINARY_ASSET_MAX_SIZE_BYTES=25000000
ORION_BINARY_ASSET_ALLOWED_MIME_TYPES=["image/png","image/jpeg","image/webp"]
ORION_BINARY_ASSET_ALLOWED_EXTENSIONS=["png","jpg","jpeg","webp"]
```

Estas opciones son globales; no forman parte de la configuración pública de un job. No contienen
secretos ni configuración de modelos.

## Reconciliación

`FilesystemBinaryAssetReconciler` inspecciona únicamente directorios contractuales
`production/<uuid>/assets/images`. Es de solo diagnóstico: reporta archivo sin metadata,
metadata sin archivo, metadata inválida, checksum/tamaño incorrectos, MIME inválido, corrupción,
links inseguros y paths inseguros. El reconciliador común de Production incluye sus conteos sin
convertirse en un escáner arbitrario del workspace.

Las pruebas usan imágenes locales diminutas y `MockTransport`; no realizan red ni llamadas
facturables. La infraestructura no implementa descarga, thumbnails, embeddings, visión, audio,
timeline, render, DaVinci ni frontend.
