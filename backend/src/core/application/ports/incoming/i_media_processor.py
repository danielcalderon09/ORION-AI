"""Media processor interface (port)."""

from pathlib import Path
from typing import Iterator, Protocol

import numpy as np


class IMediaProcessor(Protocol):
    """Port for media processing operations."""

    def extract_frames(self, video_path: Path, fps: float = 1.0) -> Iterator[np.ndarray]: ...
    def extract_audio(self, video_path: Path, output_path: Path, sample_rate: int = 16000) -> Path: ...
    def get_metadata(self, video_path: Path) -> dict: ...
    def render_clip(self, video_path: Path, edit_decisions: dict, output_path: Path) -> Path: ...
