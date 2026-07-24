"""Bounded subprocess execution, timeout, cancellation, and cleanup."""

import asyncio
import socket

import pytest

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipIntegrityError,
    VideoClipProviderResponseException,
    VideoClipProviderTimeoutException,
)
from backend.src.production.video_clip_generation.media_probe import FFprobeMediaProbe
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.tests.unit.production.video_clip_generation.test_provider_and_store import (
    request,
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hangs: bool = False,
        returncode: int = 0,
    ) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(stderr)
        if not hangs:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        self._hangs = hangs
        self._final_returncode = returncode
        self._released = asyncio.Event()
        self.returncode = None
        self.terminated = False
        self.killed = False

    async def wait(self):
        if self._hangs:
            await self._released.wait()
        self.returncode = self._final_returncode
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._final_returncode = -15
        self._released.set()

    def kill(self):
        self.killed = True
        self._final_returncode = -9
        self._released.set()


@pytest.mark.asyncio
async def test_ffmpeg_timeout_terminates_process(monkeypatch) -> None:
    process = FakeProcess(hangs=True)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    provider = SimulatedVideoClipGenerationProvider(timeout_seconds=0.01)
    with pytest.raises(VideoClipProviderTimeoutException):
        await provider._execute(("ffmpeg",))
    assert process.terminated is True


@pytest.mark.asyncio
async def test_ffmpeg_stderr_is_bounded_and_not_exposed(monkeypatch) -> None:
    private_detail = b"C:\\private\\workspace\\secret " + b"x" * 70_000
    process = FakeProcess(stderr=private_detail)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    provider = SimulatedVideoClipGenerationProvider()
    with pytest.raises(VideoClipProviderResponseException) as captured:
        await provider._execute(("ffmpeg",))
    assert "private" not in str(captured.value)
    assert "workspace" not in str(captured.value)


@pytest.mark.asyncio
async def test_ffprobe_timeout_and_output_limit_are_typed(monkeypatch, tmp_path) -> None:
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"placeholder")
    processes = [
        FakeProcess(hangs=True),
        FakeProcess(stdout=b"x" * 1_000_001),
    ]

    async def create(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(VideoClipProviderTimeoutException):
        await FFprobeMediaProbe(timeout_seconds=0.01).inspect(target)
    with pytest.raises(VideoClipIntegrityError):
        await FFprobeMediaProbe().inspect(target)


@pytest.mark.asyncio
async def test_provider_temp_directory_is_cleaned_on_cancellation(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    provider = SimulatedVideoClipGenerationProvider()

    async def cancelled(command):
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "_execute", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await provider.generate_clip(request())
    assert not list(tmp_path.glob("orion-video-clip-*"))


@pytest.mark.asyncio
async def test_provider_is_offline_and_enforces_output_limit(
    monkeypatch,
) -> None:
    def forbidden_network(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    provider = SimulatedVideoClipGenerationProvider(max_output_bytes=1)
    with pytest.raises(VideoClipProviderResponseException):
        await provider.generate_clip(request())
