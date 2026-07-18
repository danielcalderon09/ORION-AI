"""Director of Photography AI - Visual execution decisions."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.agents.dop_agent.application.ports.i_reframe_providers import (
    IFaceDetectionProvider,
    ISubjectTrackingProvider,
    IAutoReframeProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class DoPConfig:
    target_width: int = 1080
    target_height: int = 1920
    tracking_enabled: bool = True  # Sprint 1+ : auto reframe via face tracking
    stabilization_enabled: bool = False


class DoPAgent(IAgent):
    """Agent responsible for visual framing and composition.

    Supports two modes:
    - tracking_enabled=True (default): Uses face detection + subject tracking
      to compute intelligent dynamic crop boxes.
    - tracking_enabled=False: Falls back to static center crop.
    """

    def __init__(
        self,
        config: DoPConfig | None = None,
        face_detection: IFaceDetectionProvider | None = None,
        subject_tracker: ISubjectTrackingProvider | None = None,
        auto_reframe: IAutoReframeProvider | None = None,
    ):
        self.config = config or DoPConfig()
        self.face_detection = face_detection
        self.subject_tracker = subject_tracker
        self.auto_reframe = auto_reframe

    @property
    def agent_id(self) -> str:
        return "dop_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PRODUCTION

    def get_capabilities(self) -> list[str]:
        caps = [
            "vertical_reframing",
            "subject_centering",
            "crop_decision",
            "composition",
        ]
        if self.config.tracking_enabled and self.auto_reframe is not None:
            caps.extend(["auto_reframe", "face_tracking", "subject_tracking"])
        return caps

    async def execute(self, input_data: AgentInput) -> AgentResult:
        context = input_data.context or {}
        vision = context.get("vision_features", {})
        edit_decisions = context.get("edit_decisions", [])
        video_path = input_data.media_reference

        framed_decisions = []
        for decision in edit_decisions:
            if self.config.tracking_enabled and self.auto_reframe is not None:
                framing = await self._compute_framing_with_tracking(decision, video_path, vision)
            else:
                framing = self._compute_framing_static(decision, vision)
            framed_decisions.append({
                **decision,
                "framing": framing,
            })

        features = {
            "framed_decisions": framed_decisions,
            "target_resolution": {
                "width": self.config.target_width,
                "height": self.config.target_height,
            },
            "tracking_enabled": self.config.tracking_enabled,
        }

        duration = vision.get("duration_seconds", 0)
        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="1.0.0",  # bumped for auto reframe
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )

    async def _compute_framing_with_tracking(self, decision: dict, video_path: str, vision: dict) -> dict:
        """Compute dynamic framing using face/subject tracking."""
        temporal_range = decision.get("temporal_range", (0.0, 10.0))
        video_info = vision.get("video_info", {})
        try:
            target_w = self.config.target_width
            target_h = self.config.target_height
            if target_w <= 0 or target_h <= 0:
                raise ValueError(f"Invalid target dimensions: {target_w}x{target_h}")

            crop_boxes = await self.auto_reframe.compute_reframe(
                video_path=video_path,
                temporal_range=temporal_range,
                target_width=target_w,
                target_height=target_h,
                video_width=video_info.get("width"),
                video_height=video_info.get("height"),
            )
            if not crop_boxes:
                raise ValueError("No crop boxes returned from auto reframe")

            # Use the first (or most representative) crop box for the clip
            # In the future, keyframes could be passed to FFmpeg for dynamic pan
            primary = crop_boxes[len(crop_boxes) // 2] if len(crop_boxes) > 1 else crop_boxes[0]

            return {
                "x": primary.x,
                "y": primary.y,
                "width": primary.width,
                "height": primary.height,
                "target_width": target_w,
                "target_height": target_h,
                "scale_filter": "lanczos",
                "tracking_enabled": True,
                "crop_boxes_count": len(crop_boxes),
                "confidence": primary.confidence,
            }
        except (ValueError, RuntimeError, OSError, asyncio.CancelledError) as e:
            logger.warning(f"Auto reframe failed for {decision}: {e}. Falling back to center crop.")
            return self._compute_framing_static(decision, vision)

    def _compute_framing_static(self, decision: dict, vision: dict) -> dict:
        """Compute static center crop parameters for vertical video."""
        video_width = vision.get("video_info", {}).get("width", 1920)
        video_height = vision.get("video_info", {}).get("height", 1080)

        target_w = self.config.target_width
        target_h = self.config.target_height
        if target_h <= 0:
            target_h = 1920
        if target_w <= 0:
            target_w = 1080
        target_ratio = target_w / target_h  # 9/16

        # Calculate crop dimensions maintaining aspect ratio
        if video_width / video_height > target_ratio:
            # Video is wider than target: crop width
            crop_h = video_height
            crop_w = int(video_height * target_ratio)
        else:
            # Video is taller: crop height
            crop_w = video_width
            crop_h = int(video_width / target_ratio)

        # Center crop by default
        x = int((video_width - crop_w) / 2)
        y = int((video_height - crop_h) / 2)

        return {
            "x": x,
            "y": y,
            "width": crop_w,
            "height": crop_h,
            "target_width": target_w,
            "target_height": target_h,
            "scale_filter": "lanczos",
            "tracking_enabled": False,
        }
