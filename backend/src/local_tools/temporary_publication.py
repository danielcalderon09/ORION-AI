"""Local-only static publication server, readiness probe, and cleanup CLI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.asset_publishing.configuration import (
    validate_dedicated_publication_root,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
)
from backend.src.production.asset_publishing.publishers.filesystem import (
    FilesystemPublisher,
)
from backend.src.production.asset_publishing.serialization import (
    _reject_constant,
    _reject_duplicates,
)
from backend.src.production.asset_publishing.url_validation import (
    validate_public_https_url,
)
from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement

DEFAULT_BIND_ADDRESS = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_FILE_BYTES = 25_000_000
DEFAULT_READINESS_TIMEOUT_SECONDS = 15.0
_BUFFER_SIZE = 64 * 1024
_PUBLICATION_NAME = re.compile(r"^pub-[a-f0-9]{32}\.(?:png|jpe?g|webp)$")
_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_PROBE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


class TemporaryPublicationError(RuntimeError):
    """Safe operator-facing failure without URL, path, or response-body leakage."""


@dataclass(frozen=True)
class ServedPublication:
    name: str
    content_type: str
    content: bytes = field(repr=False)
    sha256: str


class PublicationResolver:
    """Resolve only active publisher-created image pairs below one confined root."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 1 <= max_file_bytes <= 250_000_000:
            raise ValueError("temporary publication file limit is invalid")
        self._root = validate_dedicated_publication_root(root)
        self._confinement = WorkspaceConfinement(self._root)
        self._maximum = max_file_bytes
        self._clock = clock

    def resolve(self, request_target: str) -> ServedPublication:
        name = _publication_name_from_target(request_target)
        target = self._confinement.resolve(name, require_exists=True)
        sidecar = self._confinement.resolve(
            f"{name.rsplit('.', 1)[0]}.publication.json",
            require_exists=True,
        )
        try:
            self._confinement.reject_unsafe_file(target)
            self._confinement.reject_unsafe_file(sidecar)
            receipt = _read_receipt(sidecar)
            content = _read_file_bounded(target, self._maximum)
        except (BinaryAssetError, OSError, UnicodeError, ValueError) as exc:
            raise TemporaryPublicationError("published asset failed local validation") from exc
        extension = name.rsplit(".", 1)[1]
        expected_type = _CONTENT_TYPES[extension]
        digest = hashlib.sha256(content).hexdigest()
        now = self._aware_now()
        if (
            receipt.publication_id != name.rsplit(".", 1)[0]
            or receipt.publisher != "filesystem"
            or receipt.content_type != expected_type
            or receipt.size_bytes != len(content)
            or receipt.source_hash != digest
            or receipt.expires_at <= now
        ):
            raise TemporaryPublicationError("published asset contract is invalid or expired")
        _validate_image_signature(content, expected_type)
        return ServedPublication(
            name=name,
            content_type=expected_type,
            content=content,
            sha256=digest,
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise TemporaryPublicationError("temporary publication clock must be timezone-aware")
        return value


class TemporaryPublicAssetServer:
    """Threaded loopback-only server with an explicit lifecycle."""

    def __init__(
        self,
        *,
        root: Path,
        port: int = DEFAULT_PORT,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 0 <= port <= 65_535:
            raise ValueError("temporary publication port is invalid")
        resolver = PublicationResolver(
            root,
            max_file_bytes=max_file_bytes,
            clock=clock,
        )
        handler = _handler_factory(resolver)
        self._server = ThreadingHTTPServer((DEFAULT_BIND_ADDRESS, port), handler)
        self._server.daemon_threads = True

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        self._server.shutdown()

    def close(self) -> None:
        self._server.server_close()


class _HttpClient(Protocol):
    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> AbstractContextManager[httpx.Response]: ...


@dataclass(frozen=True)
class PublicationReadinessResult:
    publication_id: str
    sha256: str
    content_type: str
    size_bytes: int


class PublicationReadinessChecker:
    """Explicit public probe; construction and normal startup perform no I/O."""

    def __init__(self, client: _HttpClient, *, max_response_bytes: int) -> None:
        if not 1 <= max_response_bytes <= 25_000_000:
            raise ValueError("readiness response limit is invalid")
        self._client = client
        self._maximum = max_response_bytes

    def check(
        self,
        *,
        local_health_url: str,
        public_asset_url: str,
        expected_sha256: str,
        expected_content_type: str,
        publication_id: str,
    ) -> PublicationReadinessResult:
        _validate_loopback_health_url(local_health_url)
        validate_public_https_url(public_asset_url)
        if urlsplit(public_asset_url).query:
            raise TemporaryPublicationError("public publication URL cannot contain a query")
        health_status, _, health_content = self._request(
            local_health_url,
            accept="text/plain",
            maximum=1024,
        )
        if health_status != HTTPStatus.OK or health_content != b"ok\n":
            raise TemporaryPublicationError("local publication health check failed")
        status, headers, content = self._request(
            public_asset_url,
            accept=expected_content_type,
            maximum=self._maximum,
        )
        if status != HTTPStatus.OK:
            if 300 <= status < 400:
                raise TemporaryPublicationError("public publication redirected unexpectedly")
            raise TemporaryPublicationError("public publication fetch failed")
        declared = headers.get("content-type", "").split(";", 1)[0].lower()
        if declared != expected_content_type:
            raise TemporaryPublicationError("public publication MIME differs")
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            raise TemporaryPublicationError("public publication checksum differs")
        return PublicationReadinessResult(
            publication_id=publication_id,
            sha256=digest,
            content_type=declared,
            size_bytes=len(content),
        )

    def _request(
        self, url: str, *, accept: str, maximum: int
    ) -> tuple[int, httpx.Headers, bytes]:
        try:
            with self._client.stream("GET", url, headers={"Accept": accept}) as response:
                return response.status_code, response.headers, _stream_response_bounded(
                    response, maximum
                )
        except httpx.TimeoutException as exc:
            raise TemporaryPublicationError("publication readiness request timed out") from exc
        except httpx.RequestError as exc:
            raise TemporaryPublicationError("publication readiness connection failed") from exc


def _handler_factory(
    resolver: PublicationResolver,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ORION-Temporary-Assets"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            self._serve(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler contract
            self._serve(head_only=True)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802 - stdlib handler contract
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler contract
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler contract
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler contract
            self._method_not_allowed()

        def log_message(self, _format: str, *args: Any) -> None:
            del args

        def _serve(self, *, head_only: bool) -> None:
            if self.path == "/healthz":
                self._send(HTTPStatus.OK, "text/plain", b"ok\n", head_only=head_only)
                return
            try:
                publication = resolver.resolve(self.path)
            except (BinaryAssetError, TemporaryPublicationError, ValueError):
                self._send_empty(HTTPStatus.NOT_FOUND)
                return
            self._send(
                HTTPStatus.OK,
                publication.content_type,
                publication.content,
                head_only=head_only,
            )

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(
            self,
            status: HTTPStatus,
            content_type: str,
            content: bytes,
            *,
            head_only: bool,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head_only:
                stream = memoryview(content)
                for offset in range(0, len(stream), _BUFFER_SIZE):
                    self.wfile.write(stream[offset : offset + _BUFFER_SIZE])

        def _send_empty(self, status: HTTPStatus) -> None:
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _publication_name_from_target(request_target: str) -> str:
    parsed = urlsplit(request_target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in request_target:
        raise TemporaryPublicationError("publication request target is invalid")
    decoded = parsed.path
    for _ in range(3):
        decoded = unquote(decoded)
    path = PurePosixPath(decoded)
    if (
        not decoded.startswith("/assets/")
        or len(path.parts) != 3
        or path.parts[1] != "assets"
        or any(part.startswith(".") for part in path.parts)
        or ".." in path.parts
    ):
        raise TemporaryPublicationError("publication path is not contractual")
    name = path.parts[2]
    if _PUBLICATION_NAME.fullmatch(name) is None:
        raise TemporaryPublicationError("publication name is not contractual")
    return name


def _read_receipt(path: Path) -> AssetPublicationReceipt:
    content = _read_file_bounded(path, 64_000)
    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicates,
    )
    return AssetPublicationReceipt.model_validate(payload)


def _read_file_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if not content or len(content) > maximum:
        raise TemporaryPublicationError("publication file size is outside safe limits")
    return content


def _stream_response_bounded(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes(_BUFFER_SIZE):
        size += len(chunk)
        if size > maximum:
            raise TemporaryPublicationError("readiness response size is outside safe limits")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise TemporaryPublicationError("readiness response size is outside safe limits")
    return content


def _validate_image_signature(content: bytes, content_type: str) -> None:
    valid = (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        if content_type == "image/png"
        else content.startswith(b"\xff\xd8\xff")
        if content_type == "image/jpeg"
        else len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    )
    if not valid:
        raise TemporaryPublicationError("published image signature is invalid")


def _validate_loopback_health_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != DEFAULT_BIND_ADDRESS
        or parsed.path != "/healthz"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise TemporaryPublicationError("local health URL is not contractual")


def _publication_root(settings: Settings) -> Path:
    if settings.ORION_ASSET_PUBLISHING_PUBLISHER != "filesystem":
        raise TemporaryPublicationError(
            "ORION_ASSET_PUBLISHING_PUBLISHER must be filesystem"
        )
    if settings.ORION_ASSET_PUBLISHING_PUBLIC_ROOT is None:
        raise TemporaryPublicationError("ORION_ASSET_PUBLISHING_PUBLIC_ROOT is not configured")
    try:
        return validate_dedicated_publication_root(
            settings.ORION_ASSET_PUBLISHING_PUBLIC_ROOT,
            forbidden_roots=(settings.ORION_HOME, settings.PROJECTS_DIR),
        )
    except ValueError as exc:
        raise TemporaryPublicationError("publication root is not a dedicated directory") from exc


async def _create_probe(settings: Settings, root: Path) -> AssetPublicationReceipt:
    digest = hashlib.sha256(_PROBE_PNG).hexdigest()
    unique = uuid4().hex
    publisher = FilesystemPublisher(
        public_root=root,
        public_base_url=settings.ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL,
        max_asset_bytes=min(settings.ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES, 25_000_000),
    )
    try:
        return await publisher.publish(
            asset=PublishableAsset(
                asset_id=f"readiness-{unique}",
                binary_asset_id=f"readiness-{unique}",
                source_hash=digest,
                content_type="image/png",
                extension="png",
                size_bytes=len(_PROBE_PNG),
                content=_PROBE_PNG,
                source_manifest_kind="image_acquisition",
                source_manifest_sha256=digest,
                metadata={"purpose": "temporary_publication_readiness"},
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
    finally:
        await publisher.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orion-temporary-publication")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve active image publications on loopback")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    readiness = commands.add_parser(
        "readiness", help="explicitly verify loopback and public HTTPS publication"
    )
    readiness.add_argument("--port", type=int, default=DEFAULT_PORT)
    readiness.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_READINESS_TIMEOUT_SECONDS
    )
    cleanup = commands.add_parser("cleanup", help="remove only expired publication pairs")
    cleanup.set_defaults(command="cleanup")
    doctor = commands.add_parser("doctor", help="report tunnel executable availability")
    doctor.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def _run_serve(settings: Settings, port: int) -> int:
    root = _publication_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    server = TemporaryPublicAssetServer(root=root, port=port)
    print(f"ORION temporary publication server: http://{DEFAULT_BIND_ADDRESS}:{server.port}")
    print("Health: /healthz | Assets: /assets/<publication> | Stop: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()
    return 0


def _run_readiness(settings: Settings, port: int, timeout_seconds: float) -> int:
    if not 0 < timeout_seconds <= 120:
        raise TemporaryPublicationError("readiness timeout is outside safe limits")
    root = _publication_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    receipt = asyncio.run(_create_probe(settings, root))
    with httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        result = PublicationReadinessChecker(
            client,
            max_response_bytes=DEFAULT_MAX_FILE_BYTES,
        ).check(
            local_health_url=f"http://{DEFAULT_BIND_ADDRESS}:{port}/healthz",
            public_asset_url=receipt.public_url,
            expected_sha256=receipt.source_hash,
            expected_content_type=receipt.content_type,
            publication_id=receipt.publication_id,
        )
    print(
        "Temporary publication ready: "
        f"id={result.publication_id} mime={result.content_type} "
        f"size={result.size_bytes} sha256={result.sha256}"
    )
    return 0


def _run_cleanup(settings: Settings) -> int:
    root = _publication_root(settings)
    publisher = FilesystemPublisher(
        public_root=root,
        public_base_url=settings.ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL,
        max_asset_bytes=settings.ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES,
    )

    async def cleanup() -> tuple[str, ...]:
        try:
            return await publisher.cleanup_expired(now=datetime.now(UTC))
        finally:
            await publisher.close()

    removed = asyncio.run(cleanup())
    print(f"Expired publications removed: {len(removed)}")
    return 0


def _run_doctor(port: int) -> int:
    executable = shutil.which("cloudflared")
    if executable is None:
        print("cloudflared: NOT FOUND")
        print("Install it manually from: https://developers.cloudflare.com/tunnel/downloads/")
        return 1
    print("cloudflared: FOUND")
    print(f"Launch: cloudflared tunnel --url http://{DEFAULT_BIND_ADDRESS}:{port}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _run_doctor(args.port)
        settings = Settings()
        if args.command == "serve":
            return _run_serve(settings, args.port)
        if args.command == "readiness":
            return _run_readiness(settings, args.port, args.timeout_seconds)
        if args.command == "cleanup":
            return _run_cleanup(settings)
        raise TemporaryPublicationError("unknown temporary publication command")
    except (OSError, ValueError, TemporaryPublicationError) as exc:
        message = " ".join(str(exc).split())[:300]
        print(f"Temporary publication failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
