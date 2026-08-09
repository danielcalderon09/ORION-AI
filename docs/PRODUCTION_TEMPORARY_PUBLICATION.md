# Temporary HTTPS frame publication — Phase 6F.2

Veo image-to-video requires its first frame through a directly downloadable
public HTTPS URL. ORION's production pipeline still knows only a dedicated
publication root and a configured public base URL. Tunnel process control is
operator tooling, outside the production domain.

## Components and trust boundary

The explicit local tool is:

```text
python -m backend.src.local_tools.temporary_publication <command>
```

It never starts with ORION, the desktop, or the production worker. Its static
server binds only to `127.0.0.1`, defaults to port `8765`, and exposes:

```text
GET|HEAD /healthz
GET|HEAD /assets/pub-<sha-derived-id>.png
GET|HEAD /assets/pub-<sha-derived-id>.jpg
GET|HEAD /assets/pub-<sha-derived-id>.jpeg
GET|HEAD /assets/pub-<sha-derived-id>.webp
```

Every image requires the matching `*.publication.json` sidecar produced by
`FilesystemPublisher`. The server validates publication identity, expiry,
MIME/extension, size, SHA-256, image signature, regular-file status, and root
confinement before returning bytes. Sidecars themselves are never served.

Directory listing, arbitrary filenames, dotfiles, traversal, percent-encoded
traversal, queries, symlink/junction/hard-link escapes, expired entries, JSON,
databases, logs, `.env`, uploads, remote writes, and mutating methods are
rejected. Responses use `Cache-Control: no-store` and `nosniff`.

The publication root must be a dedicated directory. Drive root, repository
root, user home, Desktop, `ORION_HOME`, and `PROJECTS_DIR` themselves are
rejected; a dedicated child is allowed. Recommended Windows root:

```text
C:\Users\Daniel Calderon\AppData\Local\ORION\published-video-frames
```

## Tunnel strategy

Phase 6F.2 selects Cloudflare Quick Tunnel as the documented operator option,
not as an ORION dependency. Cloudflare documents Quick Tunnels as temporary
development tooling that needs no account and assigns a random
`*.trycloudflare.com` hostname:

- [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
- [cloudflared downloads](https://developers.cloudflare.com/tunnel/downloads/)

ORION does not install, launch, authenticate, update, or persist credentials
for `cloudflared`. Phase 6F.2 found neither `cloudflared` nor `ngrok` in the
operator PATH. On Windows, download the current 64-bit executable or MSI from
Cloudflare's official downloads page, place/install it on PATH, then verify:

```powershell
cloudflared --version
python -m backend.src.local_tools.temporary_publication doctor --port 8765
```

Quick Tunnel URLs change after process restart. If the hostname changes,
update `ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL` before readiness or generation.
The filesystem publisher safely refreshes the receipt URL/expiry while reusing
the same verified image bytes.

## Exact Windows workflow

Configure the dedicated root first. The public base may remain a non-live safe
placeholder until Cloudflare prints the actual hostname:

```dotenv
ORION_ASSET_PUBLISHING_PUBLISHER=filesystem
ORION_ASSET_PUBLISHING_PUBLIC_ROOT=C:\Users\Daniel Calderon\AppData\Local\ORION\published-video-frames
ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL=https://assets.orion.test
ORION_ASSET_PUBLISHING_LIFETIME_SECONDS=900
```

Terminal 1 — start the loopback server:

```powershell
python -m backend.src.local_tools.temporary_publication serve --port 8765
```

Terminal 2 — start the external temporary tunnel:

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

Cloudflare documents that Quick Tunnels are not supported while a
`.cloudflared/config.yaml` file is active. If one already exists, use a named
tunnel or deliberately move that configuration aside for the temporary test;
ORION never changes it automatically.

Copy only the generated `https://<random>.trycloudflare.com` origin. Replace
the `.env` public base manually; do not append `/assets` because
`FilesystemPublisher` adds it.

With both processes still running, execute the explicit readiness probe:

```powershell
python -m backend.src.local_tools.temporary_publication readiness --port 8765
```

Readiness creates one bounded, expiring PNG probe through the real filesystem
publisher, checks local `/healthz`, then fetches the probe through the configured
public HTTPS URL without redirects and verifies MIME plus exact SHA-256. This is
the only command that intentionally contacts the configured public URL. It is
never called by tests, desktop startup, or normal readiness.

## Cleanup and shutdown

Cleanup is explicit and removes only expired `pub-*` asset/sidecar pairs whose
sidecar passes the publisher contract:

```powershell
python -m backend.src.local_tools.temporary_publication cleanup
```

It does not delete `SOURCE_IMAGE`, production artifacts, unrelated files, or
active publications. Stop `cloudflared` and the local server with `Ctrl+C` when
the controlled generation session ends. Run cleanup after the configured
expiry if immediate removal is desired.

## First Veo safety policy

Keep video `simulated` and billable `false` until server, tunnel, `.env`, and
explicit readiness all succeed. The first live test must use one scene, one
4-second 720p `9:16` video, no Veo audio, maximum one paid submission, and a
USD 0.20 maximum. Polling the accepted remote ID is not another submission.

No test in this phase starts a tunnel, contacts OpenRouter, or reaches a public
host. Static server tests use loopback only; readiness tests use
`httpx.MockTransport`.
