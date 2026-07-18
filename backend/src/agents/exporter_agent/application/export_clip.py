"""Exporter Agent - Renders final clips."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
from backend.src.infrastructure.config.settings import settings


@dataclass
class ExporterConfig:
    width: int = 1080
    height: int = 1920
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 23
    preset: str = "fast"


class ExporterAgent(IAgent):
    """Agent responsible for rendering and exporting clips."""

    def __init__(
        self,
        media_adapter: FFmpegMediaAdapter,
        config: ExporterConfig | None = None,
    ):
        self.media_adapter = media_adapter
        self.config = config or ExporterConfig()

    @property
    def agent_id(self) -> str:
        return "exporter_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        return ["video_render", "vertical_export", "subtitle_burn"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        source_path = Path(context.get("source_video", input_data.media_reference))
        clip = context.get("clip", {})
        framing = context.get("framing", {})

        start = clip.get("start", 0)
        end = clip.get("end", 10)
        clip_id = clip.get("clip_id", "unknown")

        # Determine output path
        project_id = context.get("project_id", "unknown")
        output_dir = settings.PROJECTS_DIR / str(project_id) / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"clip_{clip_id}.mp4"

        # Build crop params
        crop_params = None
        if framing:
            crop_params = {
                "x": framing.get("x", "(in_w-out_w)/2"),
                "y": framing.get("y", "(in_h-out_h)/2"),
                "w": framing.get("width", "min(iw,ih*9/16)"),
                "h": framing.get("height", "min(ih,iw*16/9)"),
            }

        # Render
        rendered = self.media_adapter.render_vertical_clip(
            video_path=source_path,
            output_path=output_path,
            start_sec=start,
            end_sec=end,
            width=self.config.width,
            height=self.config.height,
            crop_params=crop_params,
        )

        features = {
            "output_path": str(rendered),
            "clip_id": clip_id,
            "duration": end - start,
            "resolution": f"{self.config.width}x{self.config.height}",
        }

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=(start, end),
            features=features,
        )
