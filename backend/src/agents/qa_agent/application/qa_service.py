"""QA Agent - Validates clips before export."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.infrastructure.config.settings import settings


class IQAProvider(Protocol):
    """Provider for quality validation."""
    async def validate(self, clip_path: Path, expected_params: dict) -> dict: ...


@dataclass
class QAConfig:
    check_resolution: bool = True
    check_codec: bool = True
    check_audio: bool = True
    check_format: bool = True
    check_subtitles: bool = False  # Sprint 2


class BasicQAProvider:
    """Basic quality validation using ffprobe."""

    async def validate(self, clip_path: Path, expected_params: dict) -> dict:
        import json
        import subprocess

        checks = []
        passed = True

        # Probe the file
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(clip_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {
                "passed": False,
                "checks": [{"name": "ffprobe", "passed": False, "error": result.stderr}],
            }

        probe = json.loads(result.stdout)
        video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)

        # Resolution check
        if expected_params.get("check_resolution", True):
            expected_w = expected_params.get("width", settings.TARGET_RESOLUTION_WIDTH)
            expected_h = expected_params.get("height", settings.TARGET_RESOLUTION_HEIGHT)
            if video_stream:
                actual_w = video_stream.get("width", 0)
                actual_h = video_stream.get("height", 0)
                res_ok = actual_w == expected_w and actual_h == expected_h
                checks.append({
                    "name": "resolution",
                    "passed": res_ok,
                    "expected": f"{expected_w}x{expected_h}",
                    "actual": f"{actual_w}x{actual_h}",
                })
                if not res_ok:
                    passed = False
            else:
                checks.append({"name": "resolution", "passed": False, "error": "No video stream"})
                passed = False

        # Codec check
        if expected_params.get("check_codec", True):
            expected_codec = expected_params.get("video_codec", settings.TARGET_VIDEO_CODEC)
            if video_stream:
                actual_codec = video_stream.get("codec_name", "unknown")
                # libx264 outputs h264
                codec_ok = actual_codec == "h264" or expected_codec in actual_codec
                checks.append({
                    "name": "video_codec",
                    "passed": codec_ok,
                    "expected": expected_codec,
                    "actual": actual_codec,
                })
                if not codec_ok:
                    passed = False
            else:
                checks.append({"name": "video_codec", "passed": False, "error": "No video stream"})
                passed = False

        # Audio check
        if expected_params.get("check_audio", True):
            has_audio = audio_stream is not None
            checks.append({
                "name": "audio_present",
                "passed": has_audio,
            })
            if not has_audio:
                passed = False

        # Format check
        if expected_params.get("check_format", True):
            expected_format = expected_params.get("format", settings.TARGET_CONTAINER)
            actual_format = probe.get("format", {}).get("format_name", "").lower()
            format_ok = expected_format in actual_format
            checks.append({
                "name": "container_format",
                "passed": format_ok,
                "expected": expected_format,
                "actual": actual_format,
            })
            if not format_ok:
                passed = False

        return {
            "passed": passed,
            "checks": checks,
        }


class QAAgent(IAgent):
    """Agent responsible for quality assurance."""

    def __init__(
        self,
        qa_provider: IQAProvider | None = None,
        config: QAConfig | None = None,
    ):
        self.qa_provider = qa_provider or BasicQAProvider()
        self.config = config or QAConfig()

    @property
    def agent_id(self) -> str:
        return "qa_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return ["quality_validation", "format_check", "codec_check"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        clip_path = Path(input_data.media_reference)
        context = input_data.context or {}
        expected = context.get("expected_params", {})

        result = await self.qa_provider.validate(clip_path, expected)

        features = {
            "validation": result,
            "clip_path": str(clip_path),
        }

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=(0.0, 0.0),
            features=features,
        )
