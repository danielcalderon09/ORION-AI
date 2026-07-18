"""FFmpeg media processing adapter."""

import json
import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np
import cv2

from backend.src.core.application.ports.incoming.i_media_processor import IMediaProcessor


class FFmpegMediaAdapter(IMediaProcessor):
    """Concrete adapter for FFmpeg media operations."""

    def extract_frames(self, video_path: Path, fps: float = 1.0) -> Iterator[np.ndarray]:
        """Extract frames at specified FPS using OpenCV + FFmpeg backend."""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = int(video_fps / fps) if fps > 0 else 1
        frame_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_count % frame_interval == 0:
                # Convert BGR to RGB
                yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_count += 1

        cap.release()

    def extract_audio(self, video_path: Path, output_path: Path, sample_rate: int = 16000) -> Path:
        """Extract audio to WAV using FFmpeg subprocess."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
        return output_path

    def get_metadata(self, video_path: Path) -> dict:
        """Get video metadata using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")
        return json.loads(result.stdout)

    def render_vertical_clip(
        self,
        video_path: Path,
        output_path: Path,
        start_sec: float,
        end_sec: float,
        width: int = 1080,
        height: int = 1920,
        crop_params: dict | None = None,
        subtitle_path: Path | None = None,
    ) -> Path:
        """Render a vertical clip with optional crop and subtitles."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = end_sec - start_sec

        # Build filter complex
        filters = []

        # Crop and scale to vertical
        if crop_params:
            x = crop_params.get("x", "(in_w-out_w)/2")
            y = crop_params.get("y", "(in_h-out_h)/2")
            w = crop_params.get("w", "min(iw,ih*9/16)")
            h = crop_params.get("h", "min(ih,iw*16/9)")
            filters.append(f"crop={w}:{h}:{x}:{y},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
        else:
            filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")

        filter_str = ",".join(filters)

        if subtitle_path and subtitle_path.exists():
            filter_str = f"{filter_str},subtitles={subtitle_path}"

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start_sec),
            "-t", str(duration),
            "-i", str(video_path),
            "-vf", filter_str,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
        ]

        cmd.append(str(output_path))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg render failed: {result.stderr}")
        return output_path

    def render_clip(self, video_path: Path, edit_decisions: dict, output_path: Path) -> Path:
        """Generic render interface (simplified for Sprint 1)."""
        return self.render_vertical_clip(
            video_path=video_path,
            output_path=output_path,
            start_sec=edit_decisions.get("start", 0),
            end_sec=edit_decisions.get("end", 10),
            crop_params=edit_decisions.get("crop"),
            subtitle_path=edit_decisions.get("subtitles"),
        )
