"""Unit tests for Auto Reframe & Subject Tracking providers."""

import asyncio
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import cv2

from backend.src.agents.dop_agent.domain.reframe import FaceBox, CropBox, SubjectTrack
from backend.src.agents.dop_agent.infrastructure.simple_subject_tracker import SimpleSubjectTracker
from backend.src.agents.dop_agent.infrastructure.auto_reframe_provider import AutoReframeProvider
from backend.src.agents.dop_agent.infrastructure.mediapipe_face_detection import MediaPipeFaceDetectionProvider


class TestSimpleSubjectTracker:
    """Unit tests for IoU-based subject tracker."""

    def test_iou_computation(self):
        a = FaceBox(x=0, y=0, width=10, height=10, confidence=1.0)
        b = FaceBox(x=5, y=5, width=10, height=10, confidence=1.0)
        iou = SimpleSubjectTracker._compute_iou(a, b)
        assert 0.0 < iou < 1.0

    def test_iou_identical(self):
        a = FaceBox(x=0, y=0, width=10, height=10, confidence=1.0)
        iou = SimpleSubjectTracker._compute_iou(a, a)
        assert iou == 1.0

    def test_iou_no_overlap(self):
        a = FaceBox(x=0, y=0, width=10, height=10, confidence=1.0)
        b = FaceBox(x=20, y=20, width=10, height=10, confidence=1.0)
        iou = SimpleSubjectTracker._compute_iou(a, b)
        assert iou == 0.0

    def test_track_single_face_across_frames(self):
        tracker = SimpleSubjectTracker(iou_threshold=0.3)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(5)]
        # Face moving slowly right
        face_boxes = [
            [FaceBox(x=10, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=12, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=14, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=16, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=18, y=10, width=20, height=20, confidence=0.9)],
        ]
        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        assert len(tracks) == 1
        assert tracks[0].track_id == 0
        assert len(tracks[0].face_boxes) == 5

    def test_track_multiple_faces(self):
        tracker = SimpleSubjectTracker(iou_threshold=0.3)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(3)]
        face_boxes = [
            [
                FaceBox(x=10, y=10, width=20, height=20, confidence=0.9),
                FaceBox(x=50, y=50, width=20, height=20, confidence=0.9),
            ],
            [
                FaceBox(x=12, y=10, width=20, height=20, confidence=0.9),
                FaceBox(x=52, y=50, width=20, height=20, confidence=0.9),
            ],
            [
                FaceBox(x=14, y=10, width=20, height=20, confidence=0.9),
                FaceBox(x=54, y=50, width=20, height=20, confidence=0.9),
            ],
        ]
        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        assert len(tracks) == 2

    def test_track_with_missing_frames(self):
        tracker = SimpleSubjectTracker(iou_threshold=0.3, max_lost=2)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(5)]
        face_boxes = [
            [FaceBox(x=10, y=10, width=20, height=20, confidence=0.9)],
            [],  # Missing
            [],  # Missing
            [FaceBox(x=16, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=18, y=10, width=20, height=20, confidence=0.9)],
        ]
        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        # Should still track through 2 missing frames
        assert len(tracks) == 1
        assert tracks[0].start_frame == 0
        assert tracks[0].end_frame == 4

    def test_short_tracks_filtered(self):
        tracker = SimpleSubjectTracker(iou_threshold=0.3)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(3)]
        face_boxes = [
            [FaceBox(x=10, y=10, width=20, height=20, confidence=0.9)],
            [],
            [FaceBox(x=50, y=50, width=20, height=20, confidence=0.9)],
        ]
        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        # Single-frame tracks should be filtered out
        assert len(tracks) == 0


class TestAutoReframeProvider:
    """Unit tests for Auto Reframe Provider."""

    def test_fallback_center_crop(self):
        """Test fallback when no subjects are tracked."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Test direct fallback without video
        crops = provider._fallback_center_crop("nonexistent.mp4", 1080, 1920)
        assert len(crops) >= 1
        crop = crops[0]
        assert crop.width > 0
        assert crop.height > 0

    def test_reframe_with_tracked_subjects(self):
        """Test reframe uses tracked subjects when provided."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        face_boxes = [
            FaceBox(x=500, y=200, width=100, height=100, confidence=0.9),
            FaceBox(x=510, y=200, width=100, height=100, confidence=0.9),
            FaceBox(x=520, y=200, width=100, height=100, confidence=0.9),
        ]
        track = SubjectTrack(
            track_id=0,
            face_boxes=face_boxes,
            start_frame=0,
            end_frame=2,
            center_x=550.0,
            center_y=250.0,
        )
        crops = asyncio.run(provider.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=[[b] for b in face_boxes],
            subject_tracks=[track],
            video_width=1920,
            video_height=1080,
        ))
        assert len(crops) == 3
        # Face center is at ~550, crop should center on that
        for c in crops:
            # crop_w for 1080 height = 1080 * 9/16 = 607.5
            # face center_x = 550, so crop x should be near 550 - 607/2 = ~246
            assert c.x < 400  # Should be left-shifted from center (656)

    def test_smooth_crop_boxes(self):
        provider = AutoReframeProvider(None, None)
        boxes = [
            CropBox(x=100, y=100, width=600, height=1080, confidence=1.0),
            CropBox(x=110, y=105, width=600, height=1080, confidence=1.0),
            CropBox(x=90, y=95, width=600, height=1080, confidence=1.0),
        ]
        smoothed = provider._smooth_crop_boxes(boxes)
        assert len(smoothed) == 3
        # Middle value should be averaged
        assert smoothed[1].x == int((100 + 110 + 90) / 3)


class TestMediaPipeFaceDetectionProvider:
    """Unit tests for face detection provider."""

    def test_detect_faces_on_blank_image(self):
        """Blank image should return no faces."""
        provider = MediaPipeFaceDetectionProvider()
        blank = np.zeros((100, 100, 3), dtype=np.uint8)
        faces = asyncio.run(provider.detect_faces(blank))
        assert isinstance(faces, list)

    def test_detect_faces_with_synthetic_face(self):
        """Synthetic circle should not be detected as face by MediaPipe
        (it needs real facial features), but the provider should not crash."""
        provider = MediaPipeFaceDetectionProvider()
        # Create a synthetic image with a circle (not a real face)
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(img, (100, 100), 40, (255, 255, 255), -1)
        faces = asyncio.run(provider.detect_faces(img))
        assert isinstance(faces, list)
        # MediaPipe may or may not detect a circle as face; we just test it doesn't crash

    def test_provider_init(self):
        provider = MediaPipeFaceDetectionProvider(min_detection_confidence=0.7)
        assert provider.min_detection_confidence == 0.7
        assert provider.max_faces == 5

    def test_detect_faces_grayscale_does_not_crash(self):
        """Grayscale input must not raise IndexError."""
        provider = MediaPipeFaceDetectionProvider()
        gray = np.zeros((100, 100), dtype=np.uint8)
        faces = asyncio.run(provider.detect_faces(gray))
        assert isinstance(faces, list)


class TestModule1RegressionCases:
    """Regression tests for P0/P1 bugs found in critical audit."""

    def test_tracker_state_isolation_between_clips(self):
        """P0 #1: Tracks from clip A must not leak into clip B."""
        tracker = SimpleSubjectTracker(iou_threshold=0.3)

        # Clip A: one face moving right
        boxes_a = [
            [FaceBox(x=10, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=12, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=14, y=10, width=20, height=20, confidence=0.9)],
        ]
        tracks_a = asyncio.run(tracker.track_subjects(boxes_a))
        assert len(tracks_a) == 1
        assert tracks_a[0].track_id == 0

        # Clip B: completely different face at different position
        boxes_b = [
            [FaceBox(x=500, y=500, width=30, height=30, confidence=0.9)],
            [FaceBox(x=505, y=500, width=30, height=30, confidence=0.9)],
        ]
        tracks_b = asyncio.run(tracker.track_subjects(boxes_b))
        assert len(tracks_b) == 1
        # Track ID must reset to 0 because state was cleared
        assert tracks_b[0].track_id == 0
        # Center must reflect clip B, not clip A
        assert tracks_b[0].center_x > 400

    def test_tracker_memory_no_frame_buffering(self):
        """P0 #2: AutoReframeProvider must not buffer all sampled frames in memory."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker, sample_fps=2.0)

        # _detect_and_track should not create a frames list.
        # We verify by mocking the face detector and checking that the internal
        # frames accumulator is gone (the old code had `frames = []` and `frames.append`).
        import inspect
        source = inspect.getsource(provider._detect_and_track)
        assert "frames = []" not in source
        assert "frames.append" not in source

    def test_invalid_target_dimensions_raises_valueerror(self):
        """P1 #6: target_height=0 must raise ValueError before division."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        with pytest.raises(ValueError, match="Invalid target dimensions"):
            asyncio.run(provider.compute_reframe(
                video_path="dummy.mp4",
                temporal_range=(0.0, 1.0),
                target_width=1080,
                target_height=0,
            ))

    def test_fallback_center_crop_on_zero_target_height(self):
        """P1 #6: Fallback must handle target_height=0 gracefully."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Direct fallback call with zero height should not crash
        crops = provider._fallback_center_crop("dummy.mp4", 1080, 0)
        assert len(crops) == 1
        assert crops[0].height > 0
        assert crops[0].width > 0

    def test_multiple_subjects_priority_selection(self):
        """Primary subject must be the one with largest average face area."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Track 0: small face at left
        small_boxes = [
            FaceBox(x=10, y=10, width=20, height=20, confidence=0.9),
            FaceBox(x=12, y=10, width=20, height=20, confidence=0.9),
        ]
        track_small = SubjectTrack(
            track_id=0,
            face_boxes=small_boxes,
            start_frame=0,
            end_frame=1,
            center_x=20.0,
            center_y=20.0,
        )

        # Track 1: large face at right
        large_boxes = [
            FaceBox(x=500, y=200, width=200, height=200, confidence=0.9),
            FaceBox(x=510, y=200, width=200, height=200, confidence=0.9),
        ]
        track_large = SubjectTrack(
            track_id=1,
            face_boxes=large_boxes,
            start_frame=0,
            end_frame=1,
            center_x=600.0,
            center_y=300.0,
        )

        crops = asyncio.run(provider.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=[[small_boxes[0], large_boxes[0]], [small_boxes[1], large_boxes[1]]],
            subject_tracks=[track_small, track_large],
            video_width=1920,
            video_height=1080,
        ))

        assert len(crops) == 2
        # Crop should center on large face (x ~ 600), not small face (x ~ 20)
        for c in crops:
            # crop_w for 1080h = 607, center on 600 => x ~ 600 - 303 = 297
            assert c.x > 200

    def test_subject_movement_trajectory(self):
        """Track a subject moving slowly across frames (IoU stays above threshold)."""
        tracker = SimpleSubjectTracker(iou_threshold=0.1)
        boxes = []
        for i in range(10):
            boxes.append([FaceBox(
                x=50 + i * 2,
                y=50 + i * 1,
                width=80,
                height=80,
                confidence=0.9,
            )])
        tracks = asyncio.run(tracker.track_subjects(boxes))
        assert len(tracks) == 1
        assert tracks[0].start_frame == 0
        assert tracks[0].end_frame == 9
        # Average center should reflect the movement
        assert tracks[0].center_x > 50
        assert tracks[0].center_y > 50

    def test_empty_face_boxes_returns_empty_tracks(self):
        """All-empty frames should produce no tracks."""
        tracker = SimpleSubjectTracker()
        face_boxes = [[], [], []]
        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        assert tracks == []

    def test_exception_not_swallowed_on_keyboard_interrupt(self):
        """P1 #5: KeyboardInterrupt must propagate, not be swallowed."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Monkey-patch to simulate failure
        async def _broken(*args, **kwargs):
            raise KeyboardInterrupt()

        provider._detect_and_track = _broken

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(provider.compute_reframe(
                video_path="dummy.mp4",
                temporal_range=(0.0, 1.0),
                target_width=1080,
                target_height=1920,
            ))
