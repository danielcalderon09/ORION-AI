"""Vision Agent - Extracts visual features from video."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
from backend.src.infrastructure.config.settings import settings


class IObjectDetectionProvider(Protocol):
    """Provider for object detection models."""
    async def detect(self, frame) -> list[dict]: ...


class ISceneDetectionProvider(Protocol):
    """Provider for scene change detection."""
    async def detect_scenes(self, video_path: Path) -> list[dict]: ...


class IVisualFeatureProvider(Protocol):
    """Provider for general visual feature extraction."""
    async def extract_features(self, frame) -> dict: ...


@dataclass
class VisionAgentConfig:
    fps: float = 1.0
    detect_objects: bool = False  # Sprint 1: disabled by default (requires YOLO)
    detect_scenes: bool = True
    extract_embeddings: bool = False


class DummyObjectDetectionProvider:
    """Placeholder provider for testing."""
    async def detect(self, frame) -> list[dict]:
        return []


class OpenCVSceneDetectionProvider:
    """Heuristic scene detection using OpenCV histogram comparison."""

    async def detect_scenes(self, video_path: Path) -> list[dict]:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        scenes = []
        prev_hist = None
        frame_idx = 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            hist = cv2.normalize(hist, hist).flatten()

            if prev_hist is not None:
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                if diff > settings.SCENE_CHANGE_THRESHOLD:
                    timestamp = frame_idx / fps
                    scenes.append({
                        "frame": frame_idx,
                        "timestamp": timestamp,
                        "score": float(diff),
                    })

            prev_hist = hist
            frame_idx += 1

        cap.release()
        return scenes


class VisionAgent(IAgent):
    """Agent responsible for visual perception."""

    def __init__(
        self,
        media_adapter: FFmpegMediaAdapter,
        scene_provider: ISceneDetectionProvider | None = None,
        object_provider: IObjectDetectionProvider | None = None,
        config: VisionAgentConfig | None = None,
    ):
        self.media_adapter = media_adapter
        self.scene_provider = scene_provider or OpenCVSceneDetectionProvider()
        self.object_provider = object_provider or DummyObjectDetectionProvider()
        self.config = config or VisionAgentConfig()

    @property
    def agent_id(self) -> str:
        return "vision_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PERCEPTION

    def get_capabilities(self) -> list[str]:
        caps = ["frame_extraction"]
        if self.config.detect_scenes:
            caps.append("scene_detection")
        if self.config.detect_objects:
            caps.append("object_detection")
        return caps

    async def execute(self, input_data: AgentInput) -> AgentResult:
        video_path = Path(input_data.media_reference)
        features: dict = {}

        # Extract frames metadata
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps if fps else 0
        cap.release()

        features["video_info"] = {
            "total_frames": total_frames,
            "fps": fps,
            "duration_seconds": duration,
            "extracted_fps": self.config.fps,
        }

        # Scene detection
        if self.config.detect_scenes:
            scenes = await self.scene_provider.detect_scenes(video_path)
            features["scene_changes"] = scenes
            features["scene_count"] = len(scenes)

        # Object detection (placeholder for Sprint 1)
        if self.config.detect_objects:
            features["objects"] = []

        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
