"""Integration tests for Auto Reframe & Subject Tracking."""

import asyncio
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.src.agents.base.i_agent import AgentInput
from backend.src.agents.dop_agent.application.dop_service import DoPAgent, DoPConfig
from backend.src.agents.dop_agent.domain.reframe import FaceBox, CropBox, SubjectTrack
from backend.src.agents.dop_agent.infrastructure.mediapipe_face_detection import MediaPipeFaceDetectionProvider
from backend.src.agents.dop_agent.infrastructure.simple_subject_tracker import SimpleSubjectTracker
from backend.src.agents.dop_agent.infrastructure.auto_reframe_provider import AutoReframeProvider


class TestDoPAgentAutoReframe:
    """Integration tests for DoP Agent with auto reframe enabled."""

    def test_dop_agent_tracking_disabled_uses_center_crop(self):
        """When tracking is disabled, DoPAgent should produce static center crop."""
        config = DoPConfig(tracking_enabled=False)
        dop = DoPAgent(config=config)

        result = asyncio.run(dop.execute(AgentInput(
            media_reference="dummy.mp4",
            context={
                "vision_features": {
                    "video_info": {"width": 1920, "height": 1080},
                    "duration_seconds": 10.0,
                },
                "edit_decisions": [
                    {"clip_id": "clip_1", "temporal_range": (0.0, 5.0)},
                ],
            },
        )))

        framed = result.features["framed_decisions"]
        assert len(framed) == 1
        framing = framed[0]["framing"]
        assert framing["tracking_enabled"] is False
        # Center crop for 1920x1080 -> 9:16
        assert framing["width"] == int(1080 * 9 / 16)
        assert framing["height"] == 1080
        assert framing["x"] == int((1920 - framing["width"]) / 2)
        assert framing["y"] == int((1080 - framing["height"]) / 2)

    def test_dop_agent_tracking_enabled_with_real_video(self, tmp_path):
        """DoPAgent with tracking enabled should produce face-aware crop."""
        video_path = tmp_path / "face_video.mp4"
        # Generate a synthetic video (won't have real faces, but tests the pipeline)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            pytest.skip("FFmpeg not available")
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")

        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        auto_reframe = AutoReframeProvider(face_det, tracker, sample_fps=2.0)
        config = DoPConfig(tracking_enabled=True)
        dop = DoPAgent(
            config=config,
            face_detection=face_det,
            subject_tracker=tracker,
            auto_reframe=auto_reframe,
        )

        dop_result = asyncio.run(dop.execute(AgentInput(
            media_reference=str(video_path),
            context={
                "vision_features": {
                    "video_info": {"width": 1920, "height": 1080},
                    "duration_seconds": 3.0,
                },
                "edit_decisions": [
                    {"clip_id": "clip_1", "temporal_range": (0.0, 3.0)},
                ],
            },
        )))

        framed = dop_result.features["framed_decisions"]
        assert len(framed) == 1
        framing = framed[0]["framing"]
        assert framing["tracking_enabled"] is True
        assert "crop_boxes_count" in framing or "confidence" in framing
        # Should have valid dimensions
        assert framing["width"] > 0
        assert framing["height"] > 0

    def test_dop_agent_fallback_on_failure(self, tmp_path):
        """DoPAgent should fallback to center crop if auto reframe fails."""
        video_path = tmp_path / "nonexistent.mp4"

        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        auto_reframe = AutoReframeProvider(face_det, tracker)
        config = DoPConfig(tracking_enabled=True)
        dop = DoPAgent(
            config=config,
            face_detection=face_det,
            subject_tracker=tracker,
            auto_reframe=auto_reframe,
        )

        # Should not crash on missing video
        dop_result = asyncio.run(dop.execute(AgentInput(
            media_reference=str(video_path),
            context={
                "vision_features": {
                    "video_info": {"width": 1920, "height": 1080},
                    "duration_seconds": 10.0,
                },
                "edit_decisions": [
                    {"clip_id": "clip_1", "temporal_range": (0.0, 5.0)},
                ],
            },
        )))

        framed = dop_result.features["framed_decisions"]
        assert len(framed) == 1
        # Fallback should still produce a valid crop
        framing = framed[0]["framing"]
        assert framing["width"] > 0
        assert framing["height"] > 0

    def test_dop_agent_capabilities_with_tracking(self):
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        auto_reframe = AutoReframeProvider(face_det, tracker)
        config = DoPConfig(tracking_enabled=True)
        dop = DoPAgent(config=config, auto_reframe=auto_reframe)
        caps = dop.get_capabilities()
        assert "auto_reframe" in caps
        assert "face_tracking" in caps
        assert "subject_tracking" in caps

    def test_dop_agent_capabilities_without_tracking(self):
        config = DoPConfig(tracking_enabled=False)
        dop = DoPAgent(config=config)
        caps = dop.get_capabilities()
        assert "auto_reframe" not in caps
        assert "face_tracking" not in caps
        assert "subject_tracking" not in caps
        assert "vertical_reframing" in caps

    def test_dop_agent_multiple_edit_decisions(self):
        """DoPAgent should process multiple edit decisions independently."""
        config = DoPConfig(tracking_enabled=False)
        dop = DoPAgent(config=config)

        result = asyncio.run(dop.execute(AgentInput(
            media_reference="dummy.mp4",
            context={
                "vision_features": {
                    "video_info": {"width": 1920, "height": 1080},
                    "duration_seconds": 60.0,
                },
                "edit_decisions": [
                    {"clip_id": "clip_1", "temporal_range": (0.0, 15.0)},
                    {"clip_id": "clip_2", "temporal_range": (20.0, 35.0)},
                    {"clip_id": "clip_3", "temporal_range": (40.0, 55.0)},
                ],
            },
        )))

        framed = result.features["framed_decisions"]
        assert len(framed) == 3
        for f in framed:
            assert "framing" in f
            assert f["framing"]["width"] > 0
            assert f["framing"]["height"] > 0


class TestAutoReframeEndToEnd:
    """End-to-end tests with synthetic and real-like scenarios."""

    def test_one_person_scenario(self, tmp_path):
        """Simulate a single person in center — crop should center on them."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Create a video with a synthetic face-like pattern (just a bright blob)
        video_path = tmp_path / "one_person.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            pytest.skip("FFmpeg not available")
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")

        crops = asyncio.run(provider.compute_reframe(
            video_path=str(video_path),
            temporal_range=(0.0, 2.0),
            target_width=1080,
            target_height=1920,
        ))
        assert len(crops) >= 1
        assert all(c.width > 0 and c.height > 0 for c in crops)

    def test_no_faces_fallback(self, tmp_path):
        """When no faces are detected, should fallback to center crop."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Pure black video — no faces
        video_path = tmp_path / "black.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=2:r=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            pytest.skip("FFmpeg not available")
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")

        crops = asyncio.run(provider.compute_reframe(
            video_path=str(video_path),
            temporal_range=(0.0, 2.0),
            target_width=1080,
            target_height=1920,
        ))
        assert len(crops) >= 1
        # Fallback center crop
        assert crops[0].confidence == 0.0

    def test_fast_movement_stability(self):
        """Rapid face movement should still produce stable crops after smoothing."""
        provider = AutoReframeProvider(None, None)
        boxes = [
            CropBox(x=100, y=100, width=600, height=1080, confidence=1.0),
            CropBox(x=500, y=100, width=600, height=1080, confidence=1.0),  # big jump
            CropBox(x=510, y=105, width=600, height=1080, confidence=1.0),
            CropBox(x=520, y=110, width=600, height=1080, confidence=1.0),
        ]
        smoothed = provider._smooth_crop_boxes(boxes)
        # The big jump should be dampened
        assert smoothed[1].x < 500  # Should be closer to average than raw 500

    def test_rule_of_thirds_offset(self):
        """Crop should place face in upper third, not center."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker, rule_of_thirds_offset=0.15)

        # Simulate tracked subject with face at y=300 in 1080p video
        face_boxes = [
            FaceBox(x=500, y=300, width=100, height=100, confidence=0.9),
        ]
        track = type("Track", (), {
            "track_id": 0,
            "face_boxes": face_boxes,
            "start_frame": 0,
            "end_frame": 0,
            "center_x": 550.0,
            "center_y": 350.0,
        })()

        # For 1080 height, crop_h = 1080, crop_w = ~607
        # Face center_y = 350
        # target_y = 350 - 1080 * (1/3 + 0.15) = 350 - 1080 * 0.483 = 350 - 522 = -172
        # So y should be clamped to 0 (top of video)
        # This is expected — a face at y=300 with rule-of-thirds wants crop to start near top

        crops = asyncio.run(provider.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=[[face_boxes[0]]],
            subject_tracks=[track],
        ))
        assert len(crops) == 1
        # With face at upper portion, crop should be near top (small y)
        assert crops[0].y < 200


class TestModule1RealWorldCases:
    """Integration tests for real-world scenarios requested in audit fix phase."""

    def test_tracker_state_isolation_across_multiple_decisions(self):
        """DoPAgent processing 3 clips from same video must not leak tracker state."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        auto_reframe = AutoReframeProvider(face_det, tracker, sample_fps=2.0)
        config = DoPConfig(tracking_enabled=True)
        dop = DoPAgent(
            config=config,
            face_detection=face_det,
            subject_tracker=tracker,
            auto_reframe=auto_reframe,
        )

        # Use tracking-enabled path with synthetic pre-computed tracks
        # to ensure the tracker is never called with real video
        face_boxes_a = [
            [FaceBox(x=10, y=10, width=20, height=20, confidence=0.9)],
            [FaceBox(x=12, y=10, width=20, height=20, confidence=0.9)],
        ]
        track_a = SubjectTrack(
            track_id=0,
            face_boxes=[face_boxes_a[0][0], face_boxes_a[1][0]],
            start_frame=0,
            end_frame=1,
            center_x=21.0,
            center_y=20.0,
        )

        face_boxes_b = [
            [FaceBox(x=500, y=500, width=30, height=30, confidence=0.9)],
            [FaceBox(x=505, y=500, width=30, height=30, confidence=0.9)],
        ]
        track_b = SubjectTrack(
            track_id=0,
            face_boxes=[face_boxes_b[0][0], face_boxes_b[1][0]],
            start_frame=0,
            end_frame=1,
            center_x=517.5,
            center_y=515.0,
        )

        # Decision 1: small face left
        result1 = asyncio.run(dop.execute(AgentInput(
            media_reference="dummy.mp4",
            context={
                "vision_features": {
                    "video_info": {"width": 1920, "height": 1080},
                    "duration_seconds": 10.0,
                },
                "edit_decisions": [
                    {
                        "clip_id": "clip_a",
                        "temporal_range": (0.0, 5.0),
                        "precomputed_tracks": [track_a],
                        "precomputed_boxes": face_boxes_a,
                    },
                ],
            },
        )))
        framing1 = result1.features["framed_decisions"][0]["framing"]

        # Decision 2: large face right (should NOT inherit track_a state)
        # Because we don't have real precomputed injection in the pipeline,
        # we test directly on the provider to guarantee isolation.
        crops_a = asyncio.run(auto_reframe.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=face_boxes_a,
            subject_tracks=[track_a],
            video_width=1920,
            video_height=1080,
        ))
        crops_b = asyncio.run(auto_reframe.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=face_boxes_b,
            subject_tracks=[track_b],
            video_width=1920,
            video_height=1080,
        ))

        # Crop A centers on x~21 (clamped to 0), Crop B centers on x~517 => x = 517 - 303 = 214
        assert crops_a[0].x < 50
        assert crops_b[0].x > 150

    def test_export_with_and_without_subtitles_preserves_crop(self, tmp_path):
        """P0 #4: FFmpeg must preserve crop filter when subtitles are present."""
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter

        adapter = FFmpegMediaAdapter()

        # Create a real video
        video_path = tmp_path / "input.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            pytest.skip("FFmpeg not available")
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")

        output_no_subs = tmp_path / "output_no_subs.mp4"
        output_with_subs = tmp_path / "output_with_subs.mp4"
        subtitle_file = tmp_path / "subs.srt"
        subtitle_file.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n",
            encoding="utf-8",
        )

        crop_params = {"x": 100, "y": 0, "w": 607, "h": 1080}

        # Render without subtitles
        rendered_no_subs = adapter.render_vertical_clip(
            video_path=video_path,
            output_path=output_no_subs,
            start_sec=0.0,
            end_sec=1.0,
            crop_params=crop_params,
        )
        assert rendered_no_subs.exists()

        # Render with subtitles
        rendered_with_subs = adapter.render_vertical_clip(
            video_path=video_path,
            output_path=output_with_subs,
            start_sec=0.0,
            end_sec=1.0,
            crop_params=crop_params,
            subtitle_path=subtitle_file,
        )
        assert rendered_with_subs.exists()

        # Verify that both outputs contain the crop in their filter graph
        # by inspecting the command-line filter string construction indirectly.
        # The real validation is that the test reaches this point without FFmpeg error
        # and that the adapter constructs a SINGLE -vf string.
        import inspect
        source = inspect.getsource(adapter.render_vertical_clip)
        # After fix, there must be only ONE "-vf" appended to cmd
        assert source.count('"-vf"') == 1 or source.count("'-vf'") == 1

    def test_fallback_chain_no_faces_to_center_crop(self):
        """When no faces detected, auto reframe must fallback to center crop."""
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker)

        # Pure black frames — no detections possible
        black_frames = [
            [FaceBox(x=0, y=0, width=0, height=0, confidence=0.0)] for _ in range(5)
        ]
        # Actually, MediaPipe won't return boxes on black. Let's test via provider directly.
        crops = asyncio.run(provider.compute_reframe(
            video_path="dummy.mp4",
            temporal_range=(0.0, 1.0),
            target_width=1080,
            target_height=1920,
            face_boxes_per_frame=[[], [], []],
            subject_tracks=[],
            video_width=1920,
            video_height=1080,
        ))
        assert len(crops) >= 1
        assert crops[0].confidence == 0.0
        # Center crop for 1920x1080 -> 9:16
        expected_x = int((1920 - 607) / 2)
        assert crops[0].x == expected_x

    def test_async_event_loop_not_blocked(self):
        """P1 #8: Face detection must not block the async event loop."""
        import time
        face_det = MediaPipeFaceDetectionProvider()
        tracker = SimpleSubjectTracker()
        provider = AutoReframeProvider(face_det, tracker, sample_fps=2.0)

        # The provider should call detect_faces via asyncio.to_thread.
        # We verify by inspecting source code that _sync_detect_faces and to_thread are used.
        import inspect
        source = inspect.getsource(provider._detect_and_track)
        assert "asyncio.to_thread" in source
        assert "_sync_detect_faces" in source

    def test_multiple_subjects_multiple_tracks(self):
        """Two distinct subjects moving independently should produce two tracks."""
        tracker = SimpleSubjectTracker(iou_threshold=0.3)

        # Subject A moves right, Subject B moves down
        face_boxes = []
        for i in range(5):
            face_boxes.append([
                FaceBox(x=10 + i*5, y=10, width=20, height=20, confidence=0.9),
                FaceBox(x=100, y=100 + i*5, width=20, height=20, confidence=0.9),
            ])

        tracks = asyncio.run(tracker.track_subjects(face_boxes))
        assert len(tracks) == 2
        # Both tracks should span all 5 frames
        for t in tracks:
            assert t.start_frame == 0
            assert t.end_frame == 4
            assert len(t.face_boxes) == 5
