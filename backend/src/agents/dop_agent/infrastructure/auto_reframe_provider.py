"""Auto Reframe Provider — Intelligent 9:16 crop with smooth camera motion.

Computes per-frame or keyframe crop boxes that keep the primary subject visible
and centered, applying rule-of-thirds and smooth panning between keyframes.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.src.agents.dop_agent.domain.reframe import FaceBox, SubjectTrack, CropBox
from backend.src.agents.dop_agent.application.ports.i_reframe_providers import IAutoReframeProvider

logger = logging.getLogger(__name__)


class AutoReframeProvider(IAutoReframeProvider):
    """Intelligent auto reframe to vertical 9:16.

    Algorithm:
    1. Detect faces in sampled frames.
    2. Track subjects across frames.
    3. Select primary subject (largest or most central).
    4. Compute crop box per keyframe keeping subject centered.
    5. Apply rule-of-thirds: eyes/face in upper third.
    6. Smooth motion between keyframes with interpolation.
    7. Fallback to center crop if no faces detected.
    """

    def __init__(
        self,
        face_detection,
        subject_tracker,
        sample_fps: float = 2.0,
        smooth_window: int = 3,
        rule_of_thirds_offset: float = 0.15,
    ):
        self.face_detection = face_detection
        self.subject_tracker = subject_tracker
        self.sample_fps = sample_fps
        self.smooth_window = smooth_window
        self.rule_of_thirds_offset = rule_of_thirds_offset

    async def compute_reframe(
        self,
        video_path: str,
        temporal_range: tuple[float, float],
        target_width: int,
        target_height: int,
        face_boxes_per_frame: list[list[FaceBox]] | None = None,
        subject_tracks: list[SubjectTrack] | None = None,
        video_width: int | None = None,
        video_height: int | None = None,
    ) -> list[CropBox]:
        """Compute crop boxes for the given temporal range.

        If face_boxes_per_frame and subject_tracks are provided, uses them directly.
        Otherwise, samples frames from video_path and runs detection/tracking.
        """
        if target_width <= 0 or target_height <= 0:
            raise ValueError(f"Invalid target dimensions: {target_width}x{target_height}")

        start_sec, end_sec = temporal_range
        duration = end_sec - start_sec

        if face_boxes_per_frame is None or subject_tracks is None:
            face_boxes_per_frame, subject_tracks = await self._detect_and_track(
                video_path, start_sec, end_sec
            )

        if not subject_tracks:
            logger.info("AutoReframe: No subjects tracked. Falling back to center crop.")
            return self._fallback_center_crop(video_path, target_width, target_height, video_width, video_height)

        # Select primary subject: largest average face area
        try:
            primary_track = max(subject_tracks, key=lambda t: sum(b.width * b.height for b in t.face_boxes))
        except (ValueError, RuntimeError) as e:
            logger.warning(f"AutoReframe: Error selecting primary track: {e}. Falling back.")
            return self._fallback_center_crop(video_path, target_width, target_height, video_width, video_height)

        # Compute target crop dimensions
        if video_width is not None and video_height is not None:
            video_w, video_h = video_width, video_height
        else:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return self._fallback_center_crop(video_path, target_width, target_height)
            try:
                video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            finally:
                cap.release()

        target_ratio = target_width / target_height
        crop_h = video_h
        crop_w = int(crop_h * target_ratio)
        if crop_w > video_w:
            crop_w = video_w
            if target_ratio > 0:
                crop_h = int(crop_w / target_ratio)
            else:
                crop_h = video_h

        # Build crop boxes from primary track
        crop_boxes = []
        for box in primary_track.face_boxes:
            # Center crop on face center
            face_cx = box.x + box.width / 2
            face_cy = box.y + box.height / 2

            # Apply rule-of-thirds: shift crop up so face is in upper third
            # target_y = face_cy - crop_h * (1/3 + rule_of_thirds_offset)
            target_y = face_cy - crop_h * (1 / 3 + self.rule_of_thirds_offset)
            target_x = face_cx - crop_w / 2

            # Clamp to video bounds
            x = int(max(0, min(target_x, video_w - crop_w)))
            y = int(max(0, min(target_y, video_h - crop_h)))

            crop_boxes.append(CropBox(
                x=x,
                y=y,
                width=crop_w,
                height=crop_h,
                confidence=box.confidence,
            ))

        # Smooth motion
        smoothed = self._smooth_crop_boxes(crop_boxes)

        logger.info(
            f"AutoReframe: {len(smoothed)} crop boxes for track {primary_track.track_id}, "
            f"duration={duration:.1f}s, video={video_w}x{video_h}, crop={crop_w}x{crop_h}"
        )
        return smoothed

    async def _detect_and_track(
        self, video_path: str, start_sec: float, end_sec: float
    ) -> tuple[list[list[FaceBox]], list[SubjectTrack]]:
        """Sample frames, detect faces, and track subjects."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return [], []

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            start_frame = int(start_sec * fps)
            end_frame = int(end_sec * fps)
            frame_interval = int(fps / self.sample_fps) if self.sample_fps > 0 else 1

            face_boxes_per_frame = []

            for frame_idx in range(start_frame, min(end_frame, total_frames), frame_interval):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret:
                    break
                boxes = await asyncio.to_thread(self._sync_detect_faces, frame)
                face_boxes_per_frame.append(boxes)
        finally:
            cap.release()

        subject_tracks = await self.subject_tracker.track_subjects(face_boxes_per_frame)
        return face_boxes_per_frame, subject_tracks

    def _sync_detect_faces(self, frame) -> list:
        """Synchronous wrapper to run async face detection in a thread pool."""
        return asyncio.run(self.face_detection.detect_faces(frame))

    def _fallback_center_crop(
        self, video_path: str, target_width: int, target_height: int,
        video_width: int | None = None, video_height: int | None = None,
    ) -> list[CropBox]:
        """Return a single center crop as fallback."""
        if target_height <= 0:
            target_height = 1920
        if target_width <= 0:
            target_width = 1080

        if video_width is not None and video_height is not None:
            video_w, video_h = video_width, video_height
        else:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return [CropBox(x=0, y=0, width=target_width, height=target_height, confidence=0.0)]
            try:
                video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            finally:
                cap.release()

        target_ratio = target_width / target_height
        if video_w / video_h > target_ratio:
            crop_h = video_h
            crop_w = int(crop_h * target_ratio)
        else:
            crop_w = video_w
            if target_ratio > 0:
                crop_h = int(crop_w / target_ratio)
            else:
                crop_h = video_h

        x = int((video_w - crop_w) / 2)
        y = int((video_h - crop_h) / 2)

        return [CropBox(x=x, y=y, width=crop_w, height=crop_h, confidence=0.0)]

    def _smooth_crop_boxes(self, boxes: list[CropBox]) -> list[CropBox]:
        """Apply moving average smoothing to crop coordinates."""
        if len(boxes) <= 1:
            return boxes

        w = min(self.smooth_window, len(boxes))
        smoothed = []
        for i in range(len(boxes)):
            window = boxes[max(0, i - w // 2) : min(len(boxes), i + w // 2 + 1)]
            avg_x = sum(b.x for b in window) / len(window)
            avg_y = sum(b.y for b in window) / len(window)
            smoothed.append(CropBox(
                x=int(avg_x),
                y=int(avg_y),
                width=boxes[i].width,
                height=boxes[i].height,
                confidence=boxes[i].confidence,
            ))
        return smoothed
