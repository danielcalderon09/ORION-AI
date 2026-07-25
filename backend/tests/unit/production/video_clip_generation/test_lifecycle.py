"""Video provider shutdown ordering and failure isolation."""

from dataclasses import replace

import pytest

from backend.src.production.composition.container import build_production_container
from backend.tests.unit.production.video_clip_generation.test_integration import (
    settings,
)


class CloseRecorder:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    async def close(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")


class DisposeRecorder:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def dispose(self) -> None:
        self.calls.append("engine")


@pytest.mark.asyncio
async def test_shutdown_continues_in_order_when_video_close_fails(tmp_path) -> None:
    built = build_production_container(settings(tmp_path))
    calls: list[str] = []
    names = (
        "video",
        "image",
        "visual",
        "scene",
        "scripting",
        "planning",
    )
    resources = tuple(CloseRecorder(name, calls, fail=name == "video") for name in names)
    controlled = replace(
        built,
        engine=DisposeRecorder(calls),
        async_resources=resources,
    )
    with pytest.raises(RuntimeError, match="video failed"):
        await controlled.aclose()
    assert calls == [*names, "engine"]
    assert len({id(resource) for resource in resources}) == len(resources)
    built.shutdown()
