# Secure Asset Publishing Infrastructure (Phase 5F.3)

## Scope

`production.asset_publishing` is a provider-independent bounded context for making
already-verified production assets temporarily reachable through a public HTTPS URL. It does not
generate images or video, call OpenRouter, upload to a cloud service, expose an HTTP endpoint, or
run automatically as a production pipeline stage.

The allowed durable inputs are verified bytes referenced by:

- `image-acquisition-manifest.json`;
- `video-clip-generation-manifest.json`;
- their `BinaryAssetStore` or `VideoClipBinaryStore` records.

`ManifestPublishableAssetCollector` converts those completed manifests into in-memory
`PublishableAsset` contracts. It re-reads bytes through the existing verified stores; clients
cannot provide paths, URLs, content types, hashes, or arbitrary bytes.

The durable output is:

```text
production/<job_id>/asset_publishing/attempt-<n>/published-assets-manifest.json
```

This capability deliberately has no `ProductionStage` and no handler registry entry in this
phase. A later workflow may call it explicitly after acquiring or generating an asset.

## Dependency direction

```text
future provider / application adapter
                 |
                 v
        AssetPublisher port
                 |
        asset_publishing context
                 |
      verified binary asset stores
```

Publishing imports no provider, OpenRouter, HTTP client, cloud SDK, API key, or remote schema.
Provider-specific adapters may depend on publishing; publishing must never depend on them.

## Contracts and publisher abstraction

All contracts are strict, immutable, extra-forbid, and schema-versioned. `PublishedAsset`
contains the durable source identity, SHA-256, content type, size, publication/expiration times,
publisher, active public URL, URL hash, status, attempt count, and allowlisted metadata.
`PublishedAssetManifest` contains sorted unique entries, source-manifest provenance, exact
summary counts, timestamps, and root status. Binary content is excluded from repr and
serialization.

`AssetPublisher` defines:

- `publish`;
- `delete`;
- `exists`;
- `get_public_url`;
- `cleanup_expired`;
- `close`.

`SignedUrlPublisher` extends the port with an explicit signed-URL refresh operation for future
implementations. `FutureCloudPublisher` is a fail-closed architecture placeholder; it contains no
vendor SDK and performs no network I/O.

Implementations in this phase:

- `NullPublisher` is the default and rejects publication.
- `FilesystemPublisher` is development-only. It atomically copies verified bytes into a
  dedicated publication root and writes a canonical receipt sidecar. It does not start or expose
  a web server. Its configured HTTPS base URL is only a mapping contract for an independently
  managed, trusted development gateway.
- `FutureCloudPublisher` always reports that no cloud publisher exists.

There is no S3, Cloudflare R2, Google Cloud Storage, Azure Blob, CDN, tunnel, or improvised public
server.

## URL and metadata security

Every active public URL must:

- use HTTPS;
- have a syntactically valid public host;
- reject localhost and `.localhost`;
- reject loopback, private, link-local, reserved, and other non-global literal IP addresses;
- reject userinfo, fragments, traversal, backslashes, control characters, `file:`, `ftp:`,
  `javascript:`, and `data:` schemes.

Validation is purely local and performs no DNS or network probe. The full URL is excluded from
repr. Manifest metadata rejects credential-like keys, absolute paths, and embedded URLs.
API keys, credentials, tokens, authorization headers, internal paths, and private URLs are never
accepted by the contracts.

The filesystem implementation emits stable, query-free URLs. No signed-URL implementation exists
in this phase. A future signed implementation must not put signatures in logs, artifacts, API
metadata, or errors, and must clear or protect the URL no later than expiration.

## Filesystem durability

The development publisher derives a deterministic publication ID from the binary asset ID and
source hash. It uses:

- a dedicated confined root;
- strict MIME-to-extension mapping for PNG, JPEG, WebP, and MP4;
- exclusive per-publication lock;
- regular-file, symlink, junction, and hard-link checks;
- temporary files in the destination directory;
- `flush`, file `fsync`, atomic `os.replace`, and directory `fsync`;
- canonical UTF-8 receipt sidecars;
- checksum and size verification on every recovery read;
- no incompatible overwrite.

If a crash leaves only the binary or only its receipt sidecar, a retry verifies the surviving
half and recreates only the missing half. It never creates a second publication for the same
source.

## State, checkpoints, and recovery

Entry states are closed:

```text
NOT_PUBLISHED -> PUBLISHING -> PUBLISHED
       ^              |             |
       |              v             v
       +----------- FAILED       EXPIRED -> REMOVED
```

The service persists:

1. the initial manifest;
2. `PUBLISHING` before invoking the publisher;
3. `PUBLISHED` only after a validated receipt;
4. `FAILED` with a safe typed code;
5. expiration and removal checkpoints during cleanup.

On restart:

- a valid `PUBLISHED` record and publication is reused;
- `PUBLISHING` plus a valid publication becomes `PUBLISHED`;
- `PUBLISHING` without a publication rolls back to `NOT_PUBLISHED` and retries without changing
  the deterministic publication ID;
- `FAILED` may retry with an incremented attempt count;
- source manifest, source hash, binary ID, MIME, size, publisher, and asset set must still match.

Manifest writes use canonical JSON, strict UTF-8, duplicate-key and NaN/Infinity rejection,
maximum size, write-to-temp, `fsync`, atomic replace, directory `fsync`, exclusive lock, and
compare-and-swap. Competing executions cannot both pass the durable checkpoint that precedes
publication.

`CancelledError` is propagated. Cancellation after the `PUBLISHING` checkpoint leaves a
recoverable state. Cancellation after bytes exist but before the final checkpoint is recovered
through `exists` and `get_public_url`.

## Expiration and cleanup

`PublishedAssetCleanupService` uses an injected UTC clock. When an active publication expires it
first checkpoints `EXPIRED` and clears `public_url`, then deletes the publication, checkpoints
`REMOVED`, and asks the publisher to remove any expired orphan receipts. A deletion failure
leaves the durable `EXPIRED` checkpoint and no active URL, so cleanup can be retried safely.

Cleanup is explicit; imports, startup, and reads never create background tasks or mutate files.

## Reconciliation

`PublishedAssetReconciler` is read-only. With injected durability verifiers it detects:

- invalid contractual manifests;
- source manifests that no longer exist;
- source binary assets that no longer exist;
- missing active URL metadata;
- expired active URLs;
- missing published bytes or sidecars;
- publisher verification failures.

It does not publish, retry, delete, clean, repair, scan unrelated workspace paths, or call a
provider. The report contains only job/attempt/asset identifiers and bounded safe descriptions,
never a URL or filesystem path.

## Configuration

Configuration is global and private:

```dotenv
ORION_ASSET_PUBLISHING_PUBLISHER=null
ORION_ASSET_PUBLISHING_PUBLIC_ROOT=
ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL=https://assets.orion.test
ORION_ASSET_PUBLISHING_LIFETIME_SECONDS=900
ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES=250000000
ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES=4000000
```

`null` remains the default. No request or job can select a publisher, path, base URL, lifetime, or
limit. Container construction performs no publication and no network I/O. The publisher is closed
once during normal production shutdown. It is appended after the historical resources in Phase
5F.3 to preserve their established positional lifecycle contract; no provider consumes it yet.

## Testing and rollback

The focused suite uses temporary roots and no sockets. It covers URL rejection, frozen contracts,
safe metadata, canonical serialization, atomic filesystem pairs, conflict detection, hard-link
rejection, expiration, cleanup, cancellation, rollback, retry, source drift, manifest CAS,
duplicate prevention, and read-only reconciliation.

Rollback is configuration-only: set `ORION_ASSET_PUBLISHING_PUBLISHER=null`. Existing manifests
and publication files are retained for audit and explicit cleanup; reconciliation never deletes
them.

## Current limits

This phase intentionally does not include video generation, OpenRouter requests, a real public
publisher, object storage, cloud credentials, CDN delivery, signed URL refresh, live validation,
audio, timeline, render, DaVinci, or frontend work.

The next phase may consume this publisher through `AssetPublisher`; it must not bypass the durable
manifest, URL policy, cost controls, or verified binary sources.
