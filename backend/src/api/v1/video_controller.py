"""Video upload and processing controller."""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import cv2
import numpy as np

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends, Form

from backend.src.core.domain.entities.video_project import VideoProject, ProjectStatus
from backend.src.core.application.services.orchestration_service import OrchestrationService
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.di.container import Container

logger = logging.getLogger(__name__)

# Handler ownership belongs to application logging configuration. Importing this
# controller must not open a process-lifetime file or mutate the user workspace.

# In-memory progress tracker (replace with Redis/DB in production)
_progress_store: dict[str, dict] = {}

# Cached Whisper model to avoid reloading on every request
_whisper_model = None

# Cached audio mean volume per video path to avoid repeated FFmpeg calls
_audio_energy_cache: dict[Path, float] = {}

def _get_whisper_model():
    """Lazy-load and cache the Whisper model across requests."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        device = settings.WHISPER_DEVICE
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        logger.info(f"[Whisper] Loading model {settings.WHISPER_MODEL_SIZE} on {device}...")
        _whisper_model = WhisperModel(
            settings.WHISPER_MODEL_SIZE,
            device=device,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
    return _whisper_model

router = APIRouter()


# --- FFmpeg fallback for real clip extraction ---

def _get_video_info(video_path: Path) -> tuple[float, int, int, bool]:
    """Get video duration (seconds), width, height, and whether audio exists using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return 0.0, 0, 0, False
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        video_stream = None
        has_audio = False
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and video_stream is None:
                video_stream = s
            elif s.get("codec_type") == "audio":
                has_audio = True
        if video_stream:
            w = int(video_stream.get("width", 0))
            h = int(video_stream.get("height", 0))
            return duration, w, h, has_audio
        return duration, 0, 0, has_audio
    except Exception:
        return 0.0, 0, 0, False


def _build_vertical_filter(video_w: int, video_h: int, target_w: int = 1080, target_h: int = 1920) -> str:
    """Build a simple FFmpeg filter string for center-crop vertical video.

    Calculates crop dimensions in Python to avoid complex FFmpeg expressions.
    """
    if video_w <= 0 or video_h <= 0:
        return f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"

    target_ratio = target_w / target_h  # 9/16 = 0.5625
    video_ratio = video_w / video_h

    if video_ratio > target_ratio:
        # Video is wider than target: crop width
        crop_h = video_h
        crop_w = int(crop_h * target_ratio)
    else:
        # Video is taller than target: crop height
        crop_w = video_w
        crop_h = int(crop_w / target_ratio)

    x = max(0, int((video_w - crop_w) / 2))
    y = max(0, int((video_h - crop_h) / 2))

    return f"crop={crop_w}:{crop_h}:{x}:{y},scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"


def _build_vertical_filter_fast(video_w: int, video_h: int, target_w: int = 1080, target_h: int = 1920) -> str:
    """Build a fast FFmpeg filter string for equal-split clips (fast bilinear, center crop)."""
    if video_w <= 0 or video_h <= 0:
        return f"scale={target_w}:{target_h}:flags=fast_bilinear:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"

    target_ratio = target_w / target_h
    video_ratio = video_w / video_h

    if video_ratio > target_ratio:
        crop_h = video_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = video_w
        crop_h = int(crop_w / target_ratio)

    x = max(0, int((video_w - crop_w) / 2))
    y = max(0, int((video_h - crop_h) / 2))

    return (
        f"crop={crop_w}:{crop_h}:{x}:{y},"
        f"scale={target_w}:{target_h}:flags=fast_bilinear:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )


def _extract_simple_clip(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    video_w: int,
    video_h: int,
    has_audio: bool,
    target_w: int = 1080,
    target_h: int = 1920,
) -> bool:
    """Fast FFmpeg-only vertical clip extraction (center crop, no per-frame tracking)."""
    logger.info(f"[Simple] Extracting clip {start:.1f}s-{end:.1f}s -> {output_path}")
    vf = _build_vertical_filter_fast(video_w, video_h, target_w, target_h)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-t", str(end - start), "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.append("-an")
    cmd.append(str(output_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        ok = result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
        if ok:
            logger.info(f"[Simple] Clip created: {output_path}")
        else:
            logger.error(f"[Simple] FFmpeg failed: {result.stderr[-2000:]}")
        return ok
    except Exception as e:
        logger.error(f"[Simple] Exception: {e}")
        return False


FaceBox = tuple[int, int, int, int, float]


def _detect_faces_sync(frame: np.ndarray, max_faces: int = 5, min_conf: float = 0.4) -> list[FaceBox]:
    """Detect faces in a frame using MediaPipe synchronously.

    Uses model_selection=1 (full range) to handle smaller / farther faces common
    in uploaded video clips, and optionally boosts low-light frames.
    """
    try:
        from mediapipe.python.solutions.face_detection import FaceDetection
    except ImportError:
        return []

    h, w = frame.shape[:2]
    # Mild brightness/contrast lift for dark/night footage
    adjusted = cv2.convertScaleAbs(frame, alpha=1.1, beta=10)
    rgb = cv2.cvtColor(adjusted, cv2.COLOR_BGR2RGB)
    with FaceDetection(min_detection_confidence=min_conf, model_selection=1) as detector:
        results = detector.process(rgb)

    faces = []
    if results and results.detections:
        for det in results.detections[:max_faces]:
            bbox = det.location_data.relative_bounding_box
            x = int(bbox.xmin * w)
            y = int(bbox.ymin * h)
            bw = int(bbox.width * w)
            bh = int(bbox.height * h)
            conf = det.score[0] if det.score else 0.0
            faces.append((max(0, x), max(0, y), min(bw, w - x), min(bh, h - y), float(conf)))
    return faces

class _OneEuroFilter:
    """One Euro Filter for smooth, low-latency tracking."""

    def __init__(self, freq: float = 30.0, mincutoff: float = 0.8, beta: float = 0.005, dcutoff: float = 1.0):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0
        self.t_prev: float | None = None

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        r = 2 * 3.141592653589793 * cutoff / freq
        return r / (r + 1)

    def filter(self, t: float, x: float) -> float:
        if self.t_prev is None:
            self.t_prev = t
            self.x_prev = x
            return x
        dt = t - self.t_prev
        freq = 1.0 / dt if dt > 0 else self.freq
        dx = (x - self.x_prev) / dt
        edx = self._alpha(self.dcutoff, freq) * dx + (1 - self._alpha(self.dcutoff, freq)) * self.dx_prev
        cutoff = self.mincutoff + self.beta * abs(edx)
        a = self._alpha(cutoff, freq)
        x_smooth = a * x + (1 - a) * self.x_prev
        self.x_prev = x_smooth
        self.dx_prev = edx
        self.t_prev = t
        return x_smooth


def _smooth_one_euro(points: list[tuple[float, float]], fps: float) -> list[tuple[float, float]]:
    """Apply One Euro smoothing to a 2D trajectory."""
    fx = _OneEuroFilter(freq=fps)
    fy = _OneEuroFilter(freq=fps)
    return [(fx.filter(i / fps, x), fy.filter(i / fps, y)) for i, (x, y) in enumerate(points)]


def _apply_velocity_prediction(
    points: list[tuple[float, float]], fps: float, lookahead: float = 0.08
) -> list[tuple[float, float]]:
    """Slightly shift crop in the direction of motion so it leads the subject."""
    if len(points) < 2:
        return points
    predicted = []
    frames_ahead = max(1, int(fps * lookahead))
    for i in range(len(points)):
        x, y = points[i]
        j = min(i + frames_ahead, len(points) - 1)
        dx = points[j][0] - x
        dy = points[j][1] - y
        # Only predict a fraction of the velocity to avoid overshoot
        predicted.append((x + dx * 0.25, y + dy * 0.25))
    return predicted


def _detect_silence(video_path: Path, noise_db: int = -45, min_duration: float = 0.3) -> list[tuple[float, float]]:
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


def _get_audio_energy_db(video_path: Path, start: float, end: float) -> float:
    """Return mean audio volume in dB for a segment. Higher (less negative) = louder.

    Uses a per-video cache to avoid spawning FFmpeg for every candidate segment.
    """
    cached = _audio_energy_cache.get(video_path)
    if cached is not None:
        return cached

    # Compute mean volume for the whole video once and cache it
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-af", "volumedetect", "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        for line in result.stderr.splitlines():
            m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", line)
            if m:
                db = float(m.group(1))
                _audio_energy_cache[video_path] = db
                return db
    except Exception:
        pass
    return -100.0


def _transcribe_video(video_path: Path) -> list[dict]:
    """Transcribe video with faster-whisper and return word-level data."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.warning("[Whisper] faster-whisper not available")
        return []

    # Skip if video has no audio
    _, _, _, has_audio = _get_video_info(video_path)
    if not has_audio:
        logger.info("[Whisper] Video has no audio, skipping transcription")
        return []

    cache_path = video_path.with_suffix(".whisper_words.json")
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"[Whisper] Loaded cached transcription: {len(data)} words")
            return data
        except Exception:
            pass

    model = _get_whisper_model()
    logger.info("[Whisper] Transcribing...")
    segments, _ = model.transcribe(
        str(video_path),
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300),
        beam_size=1,
        best_of=1,
    )

    words = []
    for seg in segments:
        for w in seg.words:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})

    words.sort(key=lambda w: w["start"])
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False)
    except Exception:
        pass

    logger.info(f"[Whisper] Transcription complete: {len(words)} words")
    return words


def _build_natural_segments(
    words: list[dict],
    silence_segments: list[tuple[float, float]],
    min_duration: float = 15.0,
    max_duration: float = 90.0,
    word_gap_threshold: float = 1.5,
) -> list[tuple[float, float]]:
    """Build content-aware segments from words and silences.

    Segments respect natural pauses and can have variable duration within bounds.
    """
    if not words:
        return []

    # Split words into natural utterances at long gaps or silences
    utterances = []
    u_start = words[0]["start"]
    u_end = words[0]["end"]

    def _has_long_silence(a: float, b: float) -> bool:
        for s_start, s_end in silence_segments:
            overlap_start = max(a, s_start)
            overlap_end = min(b, s_end)
            if overlap_end - overlap_start >= word_gap_threshold:
                return True
        return False

    for i in range(1, len(words)):
        w = words[i]
        gap = w["start"] - u_end
        if gap > word_gap_threshold or _has_long_silence(u_end, w["start"]):
            utterances.append((u_start, u_end))
            u_start = w["start"]
            u_end = w["end"]
        else:
            u_end = w["end"]
    utterances.append((u_start, u_end))

    # Merge utterances into candidate segments that satisfy duration bounds
    candidates = []
    i = 0
    while i < len(utterances):
        start, end = utterances[i]
        # Merge forward while under min_duration or while next utterance fits in max_duration
        while i + 1 < len(utterances):
            next_start, next_end = utterances[i + 1]
            if (end - start) < min_duration:
                if (next_end - start) <= max_duration:
                    end = next_end
                    i += 1
                else:
                    break
            else:
                # Current segment already long enough; decide if next is close enough to merge
                gap = next_start - end
                if gap < 1.0 and (next_end - start) <= max_duration:
                    end = next_end
                    i += 1
                else:
                    break
        if end - start >= 2.0:
            candidates.append((start, max(start + 2.0, min(end, start + max_duration))))
        i += 1

    # Filter to reasonable bounds
    filtered = []
    for start, end in candidates:
        dur = end - start
        if dur >= min_duration and dur <= max_duration:
            filtered.append((start, end))
        elif dur > max_duration:
            # Split oversized segment at natural boundaries
            pos = start
            while pos < end:
                chunk_end = min(pos + max_duration, end)
                # Try to find a silence near the desired end
                best_end = chunk_end
                for s_start, s_end in silence_segments:
                    if pos < s_start < chunk_end and (s_end - s_start) >= 0.5:
                        best_end = s_start
                        break
                if best_end - pos >= min_duration:
                    filtered.append((pos, best_end))
                elif filtered and (pos - filtered[-1][1]) < 2.0:
                    # Merge with previous if too short
                    last_start, _ = filtered[-1]
                    if best_end - last_start <= max_duration:
                        filtered[-1] = (last_start, best_end)
                    else:
                        filtered.append((pos, best_end))
                else:
                    filtered.append((pos, best_end))
                pos = best_end
    return filtered


def _score_segment(
    video_path: Path,
    start: float,
    end: float,
    video_w: int,
    video_h: int,
    silence_segments: list[tuple[float, float]],
) -> float:
    """Score a candidate segment by speech density, face quality and audio energy."""
    duration = end - start
    if duration <= 0:
        return 0.0

    # Speech density
    silence_time = 0.0
    for s_start, s_end in silence_segments:
        overlap_start = max(start, s_start)
        overlap_end = min(end, s_end)
        if overlap_end > overlap_start:
            silence_time += overlap_end - overlap_start
    speech_density = max(0.0, 1.0 - silence_time / duration)

    # Face quality: sample frames and prefer large, central faces
    cap = cv2.VideoCapture(str(video_path))
    face_score = 0.0
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        samples = max(2, min(5, int(duration / 5.0)))
        face_values = []
        for k in range(samples):
            t = start + (k + 0.5) * duration / samples
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ret, frame = cap.read()
            if not ret:
                continue
            faces = _detect_faces_sync(frame)
            if faces:
                # Score by largest face relative to frame area and centrality
                largest = max(faces, key=lambda f: f[2] * f[3])
                face_area = largest[2] * largest[3]
                frame_area = video_w * video_h
                size_score = min(1.0, face_area / (frame_area * 0.15))
                cx = largest[0] + largest[2] / 2
                cy = largest[1] + largest[3] / 2
                center_score = 1.0 - (abs(cx - video_w / 2) / (video_w / 2) + abs(cy - video_h / 2) / (video_h / 2)) / 2
                face_values.append(max(0.0, size_score * 0.7 + center_score * 0.3))
            else:
                face_values.append(0.0)
        face_score = sum(face_values) / len(face_values) if face_values else 0.0
    finally:
        cap.release()

    # Audio energy
    db = _get_audio_energy_db(video_path, start, end)
    energy_score = min(1.0, max(0.0, (db + 60.0) / 30.0))  # map -60..-30 dB to 0..1

    # Combine
    return speech_density * 0.45 + face_score * 0.35 + energy_score * 0.20


def _select_best_segments(
    candidates: list[tuple[float, float]],
    scores: list[float],
    num_clips: int,
) -> list[tuple[float, float]]:
    """Pick top N non-overlapping candidate segments by score."""
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    selected = []
    for idx, score in indexed:
        if score <= 0:
            continue
        start, end = candidates[idx]
        overlap = any(not (end <= s or start >= e) for s, e in selected)
        if not overlap:
            selected.append((start, end))
        if len(selected) >= num_clips:
            break
    selected.sort(key=lambda x: x[0])
    return selected





def _sample_speaking_face(
    video_path: Path,
    start: float,
    end: float,
    sample_fps: float = 5.0,
) -> list[tuple[float | None, float | None]]:
    """Sample frames and track the face that is most likely speaking.

    Uses face motion as a proxy for "active speaker" and keeps a persistent
    active track so the crop doesn't jump between people unnecessarily.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        start_frame = int(start * fps)
        end_frame = min(int(end * fps), total_frames)
        frame_interval = max(1, int(fps / sample_fps))

        centers: list[tuple[float | None, float | None]] = []
        prev_faces: list[dict] = []
        active_track_id = 0
        next_track_id = 1

        for frame_idx in range(start_frame, end_frame, frame_interval):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            faces = _detect_faces_sync(frame)
            if not faces:
                if centers:
                    centers.append(centers[-1])
                else:
                    centers.append((None, None))
                continue

            h, w = frame.shape[:2]
            curr_faces = []
            for f in faces:
                cx = f[0] + f[2] / 2
                cy = f[1] + f[3] / 2
                area = f[2] * f[3]
                center_score = 1.0 - (abs(cx - w / 2) / (w / 2) + abs(cy - h / 2) / (h / 2)) / 2
                curr_faces.append({"cx": cx, "cy": cy, "area": area, "center_score": center_score})

            # Match current faces to previous tracks by proximity (greedy)
            matched_prev_to_curr: dict[int, int] = {}
            used_curr = set()
            if prev_faces:
                for p_idx, p in enumerate(prev_faces):
                    best_c = None
                    best_d = float("inf")
                    for c_idx, c in enumerate(curr_faces):
                        if c_idx in used_curr:
                            continue
                        d2 = (c["cx"] - p["cx"]) ** 2 + (c["cy"] - p["cy"]) ** 2
                        if d2 < best_d:
                            best_d = d2
                            best_c = c_idx
                    if best_c is not None and best_d < (w * 0.5) ** 2:
                        matched_prev_to_curr[p_idx] = best_c
                        used_curr.add(best_c)

            # Assign track IDs to current faces
            curr_tracks: dict[int, int] = {}
            for p_idx, c_idx in matched_prev_to_curr.items():
                curr_tracks[c_idx] = prev_faces[p_idx]["track_id"]
            for c_idx, c in enumerate(curr_faces):
                if c_idx not in curr_tracks:
                    c["track_id"] = next_track_id
                    next_track_id += 1
                else:
                    c["track_id"] = curr_tracks[c_idx]

            # Compute motion score for each face relative to its matched previous track
            for c_idx, c in enumerate(curr_faces):
                c["motion"] = 0.0
                for p_idx, matched_c_idx in matched_prev_to_curr.items():
                    if matched_c_idx == c_idx:
                        p = prev_faces[p_idx]
                        d2 = (c["cx"] - p["cx"]) ** 2 + (c["cy"] - p["cy"]) ** 2
                        c["motion"] = d2 / (w * h)
                        break

            # Score faces: motion = speaking cue, size + centrality = quality
            best_idx = 0
            best_score = -1.0
            for i, c in enumerate(curr_faces):
                size_score = min(1.0, c["area"] / (w * h * 0.12))
                motion_score = min(1.0, c["motion"] / 0.008)
                score = motion_score * 0.55 + size_score * 0.30 + c["center_score"] * 0.15
                c["score"] = score
                if score > best_score:
                    best_score = score
                    best_idx = i

            # Hysteresis: keep active track unless another is clearly better
            active_curr_idx = None
            for c_idx, c in enumerate(curr_faces):
                if c["track_id"] == active_track_id:
                    active_curr_idx = c_idx
                    break
            if active_curr_idx is not None:
                active_score = curr_faces[active_curr_idx]["score"]
                if active_score >= best_score * 0.65:
                    best_idx = active_curr_idx
                else:
                    active_track_id = curr_faces[best_idx]["track_id"]
            else:
                active_track_id = curr_faces[best_idx]["track_id"]

            selected = curr_faces[best_idx]
            centers.append((selected["cx"], selected["cy"]))
            prev_faces = curr_faces

        detected = sum(1 for c in centers if c[0] is not None)
        logger.info(f"[Tracking] Tracked speaking face in {detected}/{len(centers)} sampled frames")
        return centers
    finally:
        cap.release()



def _create_sr_upsampler(scale: int = 4, model: str = "espcn") -> cv2.dnn_superres.DnnSuperResImpl | None:
    """Create an OpenCV DNN super-resolution upsampler if model is available.

    Supported models: espcn, fsrcnn.
    ESPCN is ~4x faster than FSRCNN on CPU and quality is close.
    """
    try:
        model_dir = Path.home() / ".orion" / "models"
        model_name = model.lower()
        model_path = model_dir / f"{model_name.upper()}_x{scale}.pb"
        if not model_path.exists():
            logger.warning(f"[SR] Model not found at {model_path}")
            return None
        sr = cv2.dnn_superres.DnnSuperResImpl_create()
        sr.readModel(str(model_path))
        sr.setModel(model_name, scale)
        logger.info(f"[SR] {model_name.upper()} x{scale} upsampler loaded")
        return sr
    except Exception as e:
        logger.warning(f"[SR] Failed to load super-resolution model: {e}")
        return None



def _extract_tracking_clip(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    video_w: int,
    video_h: int,
    has_audio: bool,
    target_w: int = 1080,
    target_h: int = 1920,
    profile: str = "balanced",
) -> bool:
    """Extract a vertical clip that smoothly tracks the speaking subject.

    Uses OpenCV to render the cropped video with a high-quality intermediate,
    then FFmpeg to add audio and produce the final H.264 output.
    Falls back to center crop if face detection fails completely.
    """
    logger.info(f"[Tracking] Extracting clip {start:.1f}s-{end:.1f}s -> {output_path}")

    # 1. Compute crop dimensions for 9:16 output
    target_ratio = target_w / target_h
    if video_w / video_h > target_ratio:
        crop_h = video_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = video_w
        crop_h = int(crop_w / target_ratio)

    # 2. Detect and track speaking face at 5 fps
    sample_fps = 5.0
    sample_centers = _sample_speaking_face(video_path, start, end, sample_fps=sample_fps)
    usable = [c for c in sample_centers if c[0] is not None]
    if not usable:
        logger.info("[Tracking] No faces detected, falling back to center crop")
        vf = _build_vertical_filter(video_w, video_h, target_w, target_h)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(end - start), "-i", str(video_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.append("-an")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
        except Exception as e:
            logger.error(f"[Tracking] Center crop fallback failed: {e}")
            return False

    # 3. Determine video FPS and frame range
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    start_frame = int(start * fps)
    end_frame = min(int(end * fps), total_frames)
    sample_interval = int(fps / sample_fps) if sample_fps > 0 else 1

    # 4. Fill missing samples, smooth with One-Euro, apply velocity prediction
    filled: list[tuple[float, float]] = []
    last = (video_w / 2.0, video_h / 2.0)
    for c in sample_centers:
        if c[0] is None or c[1] is None:
            filled.append(last)
        else:
            filled.append(c)
            last = c

    smoothed = _smooth_one_euro(filled, sample_fps)
    predicted = _apply_velocity_prediction(smoothed, sample_fps, lookahead=0.08)

    # 5. Render cropped video with OpenCV using high-quality MJPG intermediate
    # Enable AI upscaling only for quality profiles; it is very slow on CPU
    enable_sr = profile.lower() in {"quality", "cinematic"} or os.environ.get("ORION_FORCE_SR", "").lower() in {"1", "true", "yes"}
    sr_upsampler = _create_sr_upsampler(scale=4, model=os.environ.get("ORION_SR_MODEL", "espcn").lower()) if enable_sr else None
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False

    temp_video = output_path.with_suffix(".tmp.avi")
    try:
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(temp_video), fourcc, fps, (target_w, target_h))
        if not writer.isOpened():
            logger.error("[Tracking] Failed to open MJPG VideoWriter")
            return False

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_count = 0
        for frame_idx in range(start_frame, end_frame):
            ret, frame = cap.read()
            if not ret:
                break

            # Map frame to sample index (relative to segment start) and interpolate
            rel_frame = frame_idx - start_frame
            sample_idx = rel_frame / sample_interval
            idx_low = int(np.floor(sample_idx))
            idx_high = int(np.ceil(sample_idx))
            frac = sample_idx - idx_low

            idx_low = max(0, min(idx_low, len(predicted) - 1))
            idx_high = max(0, min(idx_high, len(predicted) - 1))
            x0, y0 = predicted[idx_low]
            x1, y1 = predicted[idx_high]
            cx = x0 * (1 - frac) + x1 * frac
            cy = y0 * (1 - frac) + y1 * frac

            # Apply rule of thirds: shift crop up so face is in upper third
            cy = cy - crop_h * 0.10

            x = int(max(0, min(cx - crop_w / 2, video_w - crop_w)))
            y = int(max(0, min(cy - crop_h / 2, video_h - crop_h)))

            cropped = frame[y:y + crop_h, x:x + crop_w]
            # Apply AI super-resolution if available to reduce pixelation
            if sr_upsampler is not None:
                try:
                    sr = sr_upsampler.upsample(cropped)
                    resized = cv2.resize(sr, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
                except Exception:
                    resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
            else:
                resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
            writer.write(resized)
            frame_count += 1

        writer.release()
        logger.info(f"[Tracking] Rendered {frame_count} frames to temp video")
    finally:
        cap.release()

    # 6. Re-encode with FFmpeg for final quality and audio
    ffmpeg_preset = "medium" if profile.lower() in {"quality", "cinematic"} else "fast"
    if has_audio:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(temp_video),
            "-ss", str(start), "-t", str(end - start), "-i", str(video_path),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", ffmpeg_preset, "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(temp_video),
            "-c:v", "libx264", "-preset", ffmpeg_preset, "-crf", "21",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p", "-an",
            str(output_path),
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if temp_video.exists():
            temp_video.unlink()
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
            logger.info(f"[Tracking] Clip created: {output_path}")
            return True
        else:
            logger.error(f"[Tracking] FFmpeg re-encode failed: {result.stderr[-2000:]}")
            return False
    except Exception as e:
        logger.error(f"[Tracking] FFmpeg re-encode exception: {e}", exc_info=True)
        return False


def _find_best_segments(
    video_path: Path,
    num_clips: int = 3,
    min_duration: float = 15.0,
    max_duration: float = 90.0,
) -> list[tuple[float, float]]:
    """Find the best video segments using Whisper + audio + face analysis.

    Segments have variable duration based on content and respect natural
    speech boundaries (pauses, sentence ends).
    """
    duration, video_w, video_h, _ = _get_video_info(video_path)
    if duration <= 0:
        return []

    # Short video: just split uniformly
    if duration < num_clips * min_duration:
        effective_clips = min(num_clips, max(1, int(duration / min_duration)))
        clip_dur = max(5.0, duration / effective_clips)
        segments = []
        for i in range(effective_clips):
            start = i * clip_dur
            end = min(start + clip_dur, duration)
            if end - start >= 5:
                segments.append((start, end))
        logger.info(f"[Segments] {len(segments)} uniform segments (short video)")
        return segments

    # Detect silences for boundaries
    silence_segments = _detect_silence(video_path)

    # Transcribe with Whisper for word-level boundaries
    words = _transcribe_video(video_path)

    if words:
        candidates = _build_natural_segments(
            words, silence_segments,
            min_duration=min_duration,
            max_duration=max_duration,
        )
    else:
        # Fallback: use silence boundaries to create candidates
        candidates = _fallback_candidates_from_silence(
            duration, silence_segments, min_duration, max_duration
        )

    if not candidates:
        logger.warning("[Segments] No candidates built, falling back to uniform")
        return _fallback_candidates_from_silence(duration, silence_segments, min_duration, max_duration)

    # Limit candidates to keep scoring fast for long videos
    max_candidates = 30
    if len(candidates) > max_candidates:
        step = len(candidates) / max_candidates
        candidates = [candidates[int(i * step)] for i in range(max_candidates)]
        logger.info(f"[Segments] Downsampled to {len(candidates)} candidate segments")

    logger.info(f"[Segments] Built {len(candidates)} natural candidate segments")

    # Score each candidate
    scores = []
    for start, end in candidates:
        score = _score_segment(video_path, start, end, video_w, video_h, silence_segments)
        scores.append(score)
        logger.info(f"[Segments] Candidate {start:.1f}s-{end:.1f}s (dur={end-start:.1f}s) score={score:.3f}")

    # Select top N non-overlapping
    selected = _select_best_segments(candidates, scores, num_clips)

    # If we didn't get enough, fill gaps with next-best non-overlapping candidates
    if len(selected) < num_clips:
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        for idx, score in indexed:
            if score <= 0:
                continue
            start, end = candidates[idx]
            if any(not (end <= s or start >= e) for s, e in selected):
                continue
            selected.append((start, end))
            if len(selected) >= num_clips:
                break

    # Final fallback if still not enough
    if not selected:
        return _fallback_candidates_from_silence(duration, silence_segments, min_duration, max_duration)

    selected.sort(key=lambda x: x[0])
    logger.info(f"[Segments] Selected {len(selected)} best segments: {selected}")
    return selected


def _fallback_candidates_from_silence(
    duration: float,
    silence_segments: list[tuple[float, float]],
    min_duration: float,
    max_duration: float,
) -> list[tuple[float, float]]:
    """Build candidate segments from silence boundaries when Whisper is unavailable."""
    # Use silence end points and start/end of video as boundaries
    boundaries = [0.0, duration]
    for s_start, s_end in silence_segments:
        boundaries.append(s_start)
        boundaries.append(s_end)
    boundaries = sorted(set(b for b in boundaries if 0 <= b <= duration))

    candidates = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        dur = end - start
        if dur >= min_duration and dur <= max_duration:
            candidates.append((start, end))
        elif dur > max_duration:
            pos = start
            while pos < end:
                chunk_end = min(pos + max_duration, end)
                if chunk_end - pos >= min_duration:
                    candidates.append((pos, chunk_end))
                pos = chunk_end
    if not candidates:
        # Uniform fallback
        n = max(1, int(duration / max_duration))
        step = duration / (n + 1)
        for i in range(n):
            start = step + i * (duration / n)
            end = min(start + max_duration, duration)
            if end - start >= min_duration:
                candidates.append((start, end))
    return candidates


def _extract_clips_fallback(
    video_path: Path,
    project_id: str,
    num_clips: int = 3,
    equal_split: bool = False,
    profile: str = "balanced",
) -> list[Path]:
    """Extract real clips from the video.

    Uses Whisper + audio + face analysis to find the best natural segments,
    then renders each as a vertical 9:16 clip with subject tracking and
    high-quality single-pass FFmpeg encoding.
    """
    exports_dir = settings.PROJECTS_DIR / project_id / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    duration, video_w, video_h, has_audio = _get_video_info(video_path)
    if duration <= 0:
        logger.warning(f"Could not determine duration for {video_path}, skipping fallback")
        return []

    logger.info(f"Video info: {video_w}x{video_h}, duration={duration:.1f}s, audio={has_audio}, equal_split={equal_split}")

    if equal_split:
        # Divide video into N equal parts (user-requested simple split mode)
        segment_duration = duration / num_clips
        best_segments = []
        for i in range(num_clips):
            start = i * segment_duration
            end = duration if i == num_clips - 1 else (i + 1) * segment_duration
            if end - start >= 1:
                best_segments.append((start, end))
        _progress_store[project_id]["stage"] = "splitting video evenly"
        _progress_store[project_id]["percent"] = 35
    else:
        # Find best segments using intelligent content analysis
        _progress_store[project_id]["stage"] = "analyzing content"
        _progress_store[project_id]["percent"] = 35
        best_segments = _find_best_segments(
            video_path,
            num_clips=num_clips,
            min_duration=15.0,
            max_duration=90.0,
        )

        if not best_segments:
            # Fallback to uniform distribution if analysis fails
            gap = duration / (num_clips + 1)
            best_segments = []
            for i in range(num_clips):
                start = gap + i * (duration / num_clips)
                end = min(start + 30.0, duration)
                if end - start >= 5:
                    best_segments.append((start, end))

    logger.info(f"[FFmpeg] Will create {len(best_segments)} clip(s) for project {project_id} (requested {num_clips})")
    created_clips: list[Path] = []
    for i, (start, end) in enumerate(best_segments):
        actual_duration = end - start
        if actual_duration < 2:
            continue

        # Update real progress so the frontend doesn't appear frozen during slow tracking
        if project_id in _progress_store:
            _progress_store[project_id]["percent"] = int(40 + (i / max(1, len(best_segments))) * 55)
            _progress_store[project_id]["stage"] = f"exporting clip {i + 1}/{len(best_segments)} ({actual_duration:.0f}s)"

        output_path = exports_dir / f"clip_{i + 1}.mp4"
        try:
            logger.info(f"[Clip {i+1}] Segment {start:.1f}s-{end:.1f}s (dur={actual_duration:.1f}s)")
            t0 = time.time()
            if equal_split:
                # Fast FFmpeg-only path for equal split mode (720x1280 for speed)
                ok = _extract_simple_clip(
                    video_path=video_path,
                    start=start,
                    end=end,
                    output_path=output_path,
                    video_w=video_w,
                    video_h=video_h,
                    has_audio=has_audio,
                    target_w=720,
                    target_h=1280,
                )
            else:
                # Use 720x1280 for speed on non-quality profiles; 1080x1920 only for quality
                if profile.lower() in {"quality", "cinematic"}:
                    target_w, target_h = 1080, 1920
                else:
                    target_w, target_h = 720, 1280
                ok = _extract_tracking_clip(
                    video_path=video_path,
                    start=start,
                    end=end,
                    output_path=output_path,
                    video_w=video_w,
                    video_h=video_h,
                    has_audio=has_audio,
                    target_w=target_w,
                    target_h=target_h,
                    profile=profile,
                )
            elapsed = time.time() - t0
            if ok and output_path.exists() and output_path.stat().st_size > 1024:
                created_clips.append(output_path)
                logger.info(f"[Clip {i+1}] Created in {elapsed:.1f}s ({output_path.stat().st_size} bytes)")
            else:
                logger.error(f"[Clip {i+1}] Tracking render failed, output missing")
        except subprocess.TimeoutExpired:
            logger.error(f"[Clip {i+1}] Timed out after 300s")
        except Exception as e:
            logger.error(f"[Clip {i+1}] Exception: {e}", exc_info=True)

    logger.info(f"[FFmpeg] Fallback complete: {len(created_clips)}/{len(best_segments)} clips created for project {project_id}")
    return created_clips
def get_orchestrator():
    # Sprint 4 orchestrator with auto-improvement
    from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
    from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
    from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
    from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
    from backend.src.infrastructure.messaging.event_bus import EventBus
    from backend.src.infrastructure.config.settings import settings

    from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
    from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
    from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
    from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
    from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import NarrativeIntelligenceAgent
    from backend.src.agents.dop_agent.application.dop_service import DoPAgent
    from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
    from backend.src.agents.qa_agent.application.qa_service import QAAgent

    from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
    from backend.src.cognition.video_understanding.i_video_understanding_provider import DummyVideoUnderstandingProvider
    from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator

    from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import ViralScoreEngineAgent
    from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import HookOptimizerAgent
    from backend.src.viral_intelligence.retention_simulator.application.retention_simulator_agent import RetentionSimulatorAgent
    from backend.src.viral_intelligence.audience_director.application.audience_director_agent import AudienceDirectorAgent
    from backend.src.viral_intelligence.creative_director_ai.application.creative_director_agent import CreativeDirectorAgent

    from backend.src.sprint4.reflection_engine.application.reflection_engine_agent import ReflectionEngineAgent
    from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent
    from backend.src.sprint4.multi_candidate_generator.application.multi_candidate_generator_agent import MultiCandidateGeneratorAgent
    from backend.src.sprint4.consensus_engine.application.consensus_engine_agent import ConsensusEngineAgent
    from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import FileSystemCreativeMemory
    from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import FileSystemFeedbackCollector, SimpleFeedbackLearner

    from backend.src.agents.dop_agent.application.dop_service import DoPAgent, DoPConfig
    from backend.src.agents.dop_agent.infrastructure.mediapipe_face_detection import MediaPipeFaceDetectionProvider
    from backend.src.agents.dop_agent.infrastructure.simple_subject_tracker import SimpleSubjectTracker
    from backend.src.agents.dop_agent.infrastructure.auto_reframe_provider import AutoReframeProvider

    media = FFmpegMediaAdapter()
    fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
    vu_agent = VideoUnderstandingAgent(DummyVideoUnderstandingProvider())
    feedback_collector = FileSystemFeedbackCollector()

    # Auto Reframe providers for Module 1
    face_detection = MediaPipeFaceDetectionProvider(min_detection_confidence=0.5)
    subject_tracker = SimpleSubjectTracker(iou_threshold=0.3, max_lost=5)
    auto_reframe = AutoReframeProvider(
        face_detection=face_detection,
        subject_tracker=subject_tracker,
        sample_fps=2.0,
    )
    dop_config = DoPConfig(tracking_enabled=True)
    dop_agent = DoPAgent(
        config=dop_config,
        face_detection=face_detection,
        subject_tracker=subject_tracker,
        auto_reframe=auto_reframe,
    )

    return Sprint5Orchestrator(
        event_bus=EventBus(),
        telemetry=TelemetryService(),
        benchmark=BenchmarkSuite(),
        vision_agent=VisionAgent(media),
        audio_agent=AudioAgent(media),
        speech_agent=SpeechAgent(),
        attention_agent=AttentionAgent(),
        narrative_agent=NarrativeIntelligenceAgent(),
        video_understanding_agent=vu_agent,
        viral_score_agent=ViralScoreEngineAgent(),
        hook_optimizer=HookOptimizerAgent(),
        retention_simulator=RetentionSimulatorAgent(),
        audience_director=AudienceDirectorAgent(),
        creative_director=CreativeDirectorAgent(),
        dop_agent=dop_agent,
        exporter_agent=ExporterAgent(media),
        qa_agent=QAAgent(),
        reflection_engine=ReflectionEngineAgent(),
        critic_ai=CriticAIAgent(),
        candidate_generator=MultiCandidateGeneratorAgent(num_variants=3),
        consensus_engine=ConsensusEngineAgent(),
        creative_memory=FileSystemCreativeMemory(),
        feedback_collector=feedback_collector,
        feedback_learner=SimpleFeedbackLearner(feedback_collector),
        feature_store=fs,
        face_detection=face_detection,
        subject_tracker=subject_tracker,
        auto_reframe=auto_reframe,
    )


@router.post("/")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    platform: str = "tiktok",
    profile: str = "balanced",
    debug: bool = False,
    clip_count: int = 3,
    equal_split: bool = False,
):
    """Upload a video and start processing for a target platform."""
    project_id = uuid4()
    project_name = Path(file.filename).stem if file.filename else "Untitled"

    # Save uploaded file in chunks to avoid loading entire file into memory
    # Use a sanitized fixed filename to avoid FFmpeg issues with special chars
    upload_dir = settings.PROJECTS_DIR / str(project_id) / "source"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / "source_video.mp4"

    logger.info(f"Receiving upload for project {project_id} -> {file_path}")
    with open(file_path, "wb") as dest:
        shutil.copyfileobj(file.file, dest)
    logger.info(f"Upload saved: {file_path} ({file_path.stat().st_size} bytes)")

    # Create project
    project = VideoProject(
        project_id=project_id,
        name=project_name,
        source_path=file_path,
    )
    project.status = ProjectStatus.INDEXING

    # Initialize progress tracker BEFORE spawning background task
    _progress_store[str(project_id)] = {"percent": 25, "stage": "uploading", "status": "processing"}

    # Start processing in background (off the main event loop)
    orchestrator = get_orchestrator()
    logger.info(f"Starting background processing for project {project_id}")

    def process_sync():
        """Synchronous processing in background thread."""
        logger.info(f"[BG] Starting sync processing for project {project_id} (requested {clip_count} clips)")
        try:
            _progress_store[str(project_id)]["stage"] = "exporting"
            _progress_store[str(project_id)]["percent"] = 60
            clips = _extract_clips_fallback(file_path, str(project_id), num_clips=clip_count, equal_split=equal_split, profile=profile)
            if clips:
                _progress_store[str(project_id)]["stage"] = "completed"
                _progress_store[str(project_id)]["percent"] = 100
                _progress_store[str(project_id)]["status"] = "completed"
                logger.info(f"[BG] FFmpeg created {len(clips)} clips for project {project_id}")
            else:
                _progress_store[str(project_id)]["stage"] = "failed"
                _progress_store[str(project_id)]["percent"] = 100
                _progress_store[str(project_id)]["status"] = "completed"
                logger.warning(f"[BG] FFmpeg produced no clips for project {project_id}")
        except Exception as e:
            logger.error(f"[BG] CRASH in sync processing for project {project_id}: {e}", exc_info=True)
            _progress_store[str(project_id)]["stage"] = "failed"
            _progress_store[str(project_id)]["percent"] = 100
            _progress_store[str(project_id)]["status"] = "completed"

    background_tasks.add_task(process_sync)

    return {
        "project_id": str(project_id),
        "name": project_name,
        "status": "processing",
        "platform": platform,
        "profile": profile,
        "debug_mode": debug,
        "clip_count": clip_count,
    }


@router.get("/health")
async def health_check():
    """Simple health check for connectivity verification."""
    return {"status": "ok", "service": "orion-ai-video"}


@router.get("/{project_id}/progress")
async def get_progress(project_id: str):
    """Get real processing progress set by the background task."""
    logger.info(f"Progress requested for project {project_id}")

    tracker = _progress_store.get(project_id)
    if tracker is None:
        return {
            "project_id": project_id,
            "stage": "unknown",
            "percent": 0,
            "status": "not_found",
        }

    return {
        "project_id": project_id,
        "stage": tracker["stage"],
        "percent": tracker["percent"],
        "status": tracker["status"],
    }


@router.get("/{project_id}")
async def get_project(project_id: str):
    """Get project status and clips."""
    tracker = _progress_store.get(project_id)
    clips = []
    if tracker and tracker.get("status") == "completed":
        # Return dummy clips for demo
        clips = [
            {"clip_id": "clip_1", "filename": "highlight_1.mp4", "path": "/tmp"},
            {"clip_id": "clip_2", "filename": "highlight_2.mp4", "path": "/tmp"},
        ]

    return {
        "project_id": project_id,
        "status": tracker["status"] if tracker else "unknown",
        "clips": clips,
    }
