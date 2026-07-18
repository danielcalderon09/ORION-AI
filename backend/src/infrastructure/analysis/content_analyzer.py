"""Fast content analyzer using FFmpeg silencedetect.

Finds best video segments by measuring how much speech/voice activity
each window contains. Much faster than frame-by-frame analysis.
"""

import re
import subprocess
from pathlib import Path

import numpy as np


def _detect_silence(video_path: Path, noise_db: int = -50, min_duration: float = 0.3) -> list[tuple[float, float]]:
    """Return list of (start, end) silence segments using FFmpeg silencedetect."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stderr
    except Exception:
        return []

    silence_segments = []
    start = None
    for line in output.splitlines():
        m_start = re.search(r"silence_start:\s*([\d.]+)", line)
        m_end = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_start:
            start = float(m_start.group(1))
        elif m_end and start is not None:
            end = float(m_end.group(1))
            silence_segments.append((start, end))
            start = None
    return silence_segments


def _get_duration(video_path: Path) -> float:
    """Get video duration via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except Exception:
        return 0.0


def find_best_segments(
    video_path: Path,
    num_clips: int = 3,
    target_duration: float = 30.0,
) -> list[tuple[float, float]]:
    """Find best segments based on voice activity.

    Uses FFmpeg silencedetect to find speech-rich windows.
    """
    duration = _get_duration(video_path)
    if duration <= 0:
        return []

    if duration < num_clips * 5:
        # Short video: uniform split
        effective_clips = min(num_clips, max(1, int(duration / 2)))
        clip_dur = max(2.0, duration / effective_clips)
        padding = duration / (effective_clips + 1)
        segments = []
        for i in range(effective_clips):
            start = padding + i * ((duration - 2 * padding) / effective_clips)
            end = min(start + clip_dur, duration)
            if end - start >= 2:
                segments.append((start, end))
        return segments

    silence_segments = _detect_silence(video_path)

    # Build speech activity array (1 = speech, 0 = silence) per second
    num_seconds = int(duration) + 1
    speech = np.ones(num_seconds, dtype=np.float32)
    for s_start, s_end in silence_segments:
        i_start = max(0, int(s_start))
        i_end = min(num_seconds, int(np.ceil(s_end)))
        speech[i_start:i_end] = 0.0

    # Build sliding windows and score by speech ratio
    window_scores = []
    step = max(1.0, target_duration / 2)
    t = 0.0
    while t + target_duration <= duration:
        start = t
        end = t + target_duration
        s_start = int(start)
        s_end = min(int(end), num_seconds)
        score = float(speech[s_start:s_end].mean()) if s_end > s_start else 0.0
        window_scores.append((start, end, score))
        t += step

    if not window_scores:
        return []

    # Sort by speech score and pick top non-overlapping
    window_scores.sort(key=lambda x: x[2], reverse=True)
    selected = []
    for start, end, score in window_scores:
        overlap = False
        for s, e in selected:
            if not (end <= s or start >= e):
                overlap = True
                break
        if not overlap:
            selected.append((start, end))
        if len(selected) >= num_clips:
            break

    # If we don't have enough speech-rich segments, fill with uniform segments
    if len(selected) < num_clips:
        existing = set(selected)
        gap = duration / (num_clips + 1)
        clip_dur = min(target_duration, duration / num_clips)
        for i in range(num_clips):
            if len(selected) >= num_clips:
                break
            start = gap + i * (duration / num_clips)
            end = min(start + clip_dur, duration)
            # Check overlap with existing
            overlap = False
            for s, e in selected:
                if not (end <= s or start >= e):
                    overlap = True
                    break
            if not overlap and (end - start) >= 2:
                selected.append((start, end))

    # Sort by start time
    selected.sort(key=lambda x: x[0])
    return selected
