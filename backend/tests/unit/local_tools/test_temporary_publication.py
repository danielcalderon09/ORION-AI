"""Loopback-only static server and mocked public readiness tests."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.src.local_tools.temporary_publication import (
    PublicationReadinessChecker,
    TemporaryPublicAssetServer,
    TemporaryPublicationError,
)
from backend.src.production.asset_publishing.configuration import (
    validate_dedicated_publication_root,
)
from backend.src.production.asset_publishing.models import PublishableAsset
from backend.src.production.asset_publishing.publishers.filesystem import (
    FilesystemPublisher,
)

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


async def _publish(root: Path, *, expires_at: datetime | None = None) -> tuple[str, str]:
    digest = hashlib.sha256(PNG).hexdigest()
    publisher = FilesystemPublisher(
        public_root=root,
        public_base_url="https://frames.example.test",
        max_asset_bytes=1_000_000,
        clock=lambda: NOW,
    )
    receipt = await publisher.publish(
        asset=PublishableAsset(
            asset_id="test-publication",
            binary_asset_id="test-publication",
            source_hash=digest,
            content_type="image/png",
            extension="png",
            size_bytes=len(PNG),
            content=PNG,
            source_manifest_kind="image_acquisition",
            source_manifest_sha256=digest,
        ),
        expires_at=expires_at or NOW + timedelta(minutes=15),
    )
    await publisher.close()
    return f"{receipt.publication_id}.png", digest


@contextmanager
def _running_server(root: Path) -> Iterator[str]:
    server = TemporaryPublicAssetServer(root=root, port=0, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        server.shutdown()
        server.close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_get_head_health_and_mime_are_closed_and_correct(tmp_path: Path) -> None:
    name, _ = await _publish(tmp_path)
    with _running_server(tmp_path) as base, httpx.Client(trust_env=False) as client:
        health = client.get(f"{base}/healthz")
        get = client.get(f"{base}/assets/{name}")
        head = client.head(f"{base}/assets/{name}")
    assert health.status_code == 200 and health.content == b"ok\n"
    assert get.status_code == 200 and get.content == PNG
    assert get.headers["content-type"] == "image/png"
    assert get.headers["cache-control"] == "no-store, max-age=0"
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(PNG)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/assets/",
        "/assets/.env",
        "/assets/unknown.png",
        "/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json",
        "/assets/../.env",
        "/assets/%2e%2e/.env",
        "/assets/%252e%252e/.env",
        "/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png?token=secret",
    ],
)
async def test_server_rejects_listing_traversal_dotfiles_and_unknowns(
    tmp_path: Path, path: str
) -> None:
    await _publish(tmp_path)
    with _running_server(tmp_path) as base, httpx.Client(trust_env=False) as client:
        response = client.get(f"{base}{path}")
    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def test_server_rejects_every_mutating_method(tmp_path: Path, method: str) -> None:
    name, _ = await _publish(tmp_path)
    with _running_server(tmp_path) as base, httpx.Client(trust_env=False) as client:
        response = client.request(method, f"{base}/assets/{name}")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


@pytest.mark.asyncio
async def test_expired_publication_is_not_served(tmp_path: Path) -> None:
    name, _ = await _publish(tmp_path, expires_at=NOW + timedelta(seconds=1))
    server_time = NOW + timedelta(seconds=2)
    server = TemporaryPublicAssetServer(root=tmp_path, port=0, clock=lambda: server_time)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{server.port}/assets/{name}")
    finally:
        server.shutdown()
        server.close()
        thread.join(timeout=5)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    name, _ = await _publish(tmp_path)
    target = tmp_path / name
    outside = tmp_path.parent / f"outside-{uuid4().hex}.png"
    outside.write_bytes(PNG)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        outside.unlink(missing_ok=True)
        pytest.skip("symlink creation is unavailable")
    try:
        with _running_server(tmp_path) as base, httpx.Client(trust_env=False) as client:
            response = client.get(f"{base}/assets/{name}")
        assert response.status_code == 404
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "broad_root",
    [Path.home(), Path.home() / "Desktop", Path(Path.cwd().anchor)],
)
def test_broad_publication_roots_are_rejected(broad_root: Path) -> None:
    with pytest.raises(ValueError, match="dedicated"):
        validate_dedicated_publication_root(broad_root)


def test_production_domain_does_not_import_local_tool_or_tunnel_vendor() -> None:
    production = Path(__file__).resolve().parents[3] / "src" / "production"
    for source in production.rglob("*.py"):
        content = source.read_text(encoding="utf-8")
        assert "backend.src.local_tools" not in content
        assert "cloudflared" not in content.lower()
        assert "ngrok" not in content.lower()


def _readiness_client(
    *,
    public_status: int = 200,
    public_content: bytes = PNG,
    public_mime: str = "image/png",
    public_headers: dict[str, str] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(
                200,
                content=b"ok\n",
                headers={"content-type": "text/plain"},
                request=request,
            )
        return httpx.Response(
            public_status,
            content=public_content,
            headers={"content-type": public_mime, **(public_headers or {})},
            request=request,
        )

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _check(client: httpx.Client, *, expected_sha256: str | None = None, maximum=1_000_000):
    return PublicationReadinessChecker(client, max_response_bytes=maximum).check(
        local_health_url="http://127.0.0.1:8765/healthz",
        public_asset_url="https://frames.example.test/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        expected_sha256=expected_sha256 or hashlib.sha256(PNG).hexdigest(),
        expected_content_type="image/png",
        publication_id="pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def test_public_readiness_success_uses_exact_hash_and_mime() -> None:
    with _readiness_client() as client:
        result = _check(client)
    assert result.sha256 == hashlib.sha256(PNG).hexdigest()
    assert result.content_type == "image/png"


@pytest.mark.parametrize(
    "url",
    [
        "http://frames.example.test/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        "https://localhost/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        "https://user:secret@frames.example.test/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        "https://frames.example.test/assets/pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png?token=secret",
    ],
)
def test_public_readiness_rejects_unsafe_public_urls_before_transport(url: str) -> None:
    with _readiness_client() as client, pytest.raises((ValueError, TemporaryPublicationError)):
        PublicationReadinessChecker(client, max_response_bytes=1_000_000).check(
            local_health_url="http://127.0.0.1:8765/healthz",
            public_asset_url=url,
            expected_sha256=hashlib.sha256(PNG).hexdigest(),
            expected_content_type="image/png",
            publication_id="pub-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (_readiness_client(public_mime="image/jpeg"), "MIME"),
        (_readiness_client(public_status=302, public_headers={"location": "https://evil.test"}), "redirected"),
        (_readiness_client(public_status=404), "fetch failed"),
    ],
)
def test_public_readiness_rejects_mime_redirect_and_missing(
    client: httpx.Client, expected: str
) -> None:
    with client, pytest.raises(TemporaryPublicationError, match=expected):
        _check(client)


def test_public_readiness_rejects_sha_mismatch_and_oversized_response() -> None:
    with _readiness_client() as client, pytest.raises(TemporaryPublicationError, match="checksum"):
        _check(client, expected_sha256="a" * 64)
    with _readiness_client(public_content=PNG * 100) as client, pytest.raises(
        TemporaryPublicationError, match="size"
    ):
        _check(client, maximum=len(PNG))


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("dns")])
def test_public_readiness_transport_failures_are_safe(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(200, content=b"ok\n", request=request)
        raise error

    with httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client, pytest.raises(TemporaryPublicationError) as captured:
        _check(client)
    message = str(captured.value)
    assert "frames.example.test" not in message
    assert "token" not in message.lower()
