"""MediaPipe Face Detection Provider — CPU-friendly, Apache 2.0."""

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.src.agents.dop_agent.domain.reframe import FaceBox
from backend.src.agents.dop_agent.application.ports.i_reframe_providers import IFaceDetectionProvider

logger = logging.getLogger(__name__)


class MediaPipeFaceDetectionProvider(IFaceDetectionProvider):
    """Face detection using MediaPipe Face Detection.

    Uses the short-range model (0.2-2m) suitable for webcam/mobile.
    Falls back to OpenCV DNN (YuNet) if MediaPipe is unavailable.
    """

    def __init__(self, min_detection_confidence: float = 0.5, max_faces: int = 5):
        self.min_detection_confidence = min_detection_confidence
        self.max_faces = max_faces
        self._detector = None
        self._fallback_detector = None
        self._use_fallback = False
        self._init_detector()

    def _init_detector(self) -> None:
        """Initialize MediaPipe face detector or fallback."""
        try:
            from mediapipe.python.solutions.face_detection import FaceDetection
            self._detector = FaceDetection(
                min_detection_confidence=self.min_detection_confidence,
                model_selection=0,  # 0=short range, 1=full range
            )
            logger.info("MediaPipe Face Detection initialized.")
        except ImportError:
            logger.warning("MediaPipe not available. Initializing OpenCV YuNet fallback.")
            self._init_fallback()

    def _init_fallback(self) -> None:
        """Initialize OpenCV YuNet face detector as fallback."""
        try:
            # YuNet is included in OpenCV 4.8+ (opencv-zoo)
            # Model path: ~/.orion/models/face_detection_yunet.onnx
            model_path = Path.home() / ".orion" / "models" / "face_detection_yunet_2023mar.onnx"
            if not model_path.exists():
                logger.warning(
                    f"YuNet model not found at {model_path}. "
                    "Face detection will be disabled. Download from opencv-zoo."
                )
                self._fallback_detector = None
                return
            self._fallback_detector = cv2.FaceDetectorYN.create(
                str(model_path),
                "",
                (320, 320),
                score_threshold=self.min_detection_confidence,
            )
            self._use_fallback = True
            logger.info("OpenCV YuNet fallback initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize face detection fallback: {e}")
            self._fallback_detector = None

    async def detect_faces(self, frame: Any) -> list[FaceBox]:
        """Detect faces in a frame."""
        if frame is None:
            return []

        if self._use_fallback or self._detector is None:
            return self._detect_faces_fallback(frame)
        return self._detect_faces_mediapipe(frame)

    def _detect_faces_mediapipe(self, frame: np.ndarray) -> list[FaceBox]:
        """Detect faces using MediaPipe."""
        import mediapipe as mp

        h, w = frame.shape[:2]
        if len(frame.shape) == 2:
            rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        elif frame.shape[2] == 3:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            rgb = frame
        results = self._detector.process(rgb)

        faces = []
        if results.detections:
            for det in results.detections[:self.max_faces]:
                bbox = det.location_data.relative_bounding_box
                x = int(bbox.xmin * w)
                y = int(bbox.ymin * h)
                bw = int(bbox.width * w)
                bh = int(bbox.height * h)
                conf = det.score[0] if det.score else 0.0
                faces.append(FaceBox(
                    x=max(0, x),
                    y=max(0, y),
                    width=min(bw, w - x),
                    height=min(bh, h - y),
                    confidence=float(conf),
                ))
        return faces

    def _detect_faces_fallback(self, frame: np.ndarray) -> list[FaceBox]:
        """Detect faces using OpenCV YuNet fallback."""
        if self._fallback_detector is None:
            return []

        h, w = frame.shape[:2]
        self._fallback_detector.setInputSize((w, h))
        _, faces = self._fallback_detector.detect(frame)

        if faces is None:
            return []

        result = []
        for f in faces[:self.max_faces]:
            # YuNet format: [x, y, w, h, x_r, y_r, x_l, y_l, nose_x, nose_y, confidence]
            x, y, bw, bh, conf = int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])
            if conf < self.min_detection_confidence:
                continue
            result.append(FaceBox(
                x=max(0, x),
                y=max(0, y),
                width=min(bw, w - x),
                height=min(bh, h - y),
                confidence=conf,
            ))
        return result
