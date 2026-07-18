"""Simple Subject Tracker — CPU-friendly IoU-based tracking.

No deep learning dependencies. Uses Intersection-over-Union (IoU) to associate
detections across frames. Suitable for face tracking and simple object tracking.
"""

import logging
from typing import Any

import numpy as np

from backend.src.agents.dop_agent.domain.reframe import FaceBox, SubjectTrack
from backend.src.agents.dop_agent.application.ports.i_reframe_providers import ISubjectTrackingProvider

logger = logging.getLogger(__name__)


class SimpleSubjectTracker(ISubjectTrackingProvider):
    """IoU-based subject tracker.

    Tracks subjects by associating face bounding boxes across consecutive frames.
    Uses Hungarian algorithm (scipy) or greedy IoU matching if scipy unavailable.
    """

    def __init__(self, iou_threshold: float = 0.3, max_lost: int = 5):
        self.iou_threshold = iou_threshold
        self.max_lost = max_lost
        self._next_track_id = 0
        self._active_tracks: dict[int, dict] = {}  # track_id -> {boxes: [], lost: int}

    async def track_subjects(self, face_boxes_per_frame: list[list[FaceBox]]) -> list[SubjectTrack]:
        """Track subjects across frames.

        Args:
            face_boxes_per_frame: List of face detections per frame.

        Returns:
            List of SubjectTrack objects.
        """
        # Reset state for each video clip to prevent cross-contamination between calls
        self._next_track_id = 0
        self._active_tracks.clear()

        if not face_boxes_per_frame:
            return []

        tracks_history: dict[int, list[FaceBox]] = {}
        track_frames: dict[int, list[int]] = {}

        for frame_idx, boxes in enumerate(face_boxes_per_frame):
            if not boxes:
                # Increment lost counter for all active tracks
                for tid in list(self._active_tracks.keys()):
                    self._active_tracks[tid]["lost"] += 1
                    if self._active_tracks[tid]["lost"] > self.max_lost:
                        del self._active_tracks[tid]
                continue

            # Match current detections to active tracks
            matched = self._match_boxes(boxes)

            for box, track_id in matched:
                if track_id is None:
                    # New track
                    track_id = self._next_track_id
                    self._next_track_id += 1
                    self._active_tracks[track_id] = {"boxes": [], "lost": 0}

                self._active_tracks[track_id]["boxes"].append(box)
                self._active_tracks[track_id]["lost"] = 0

                if track_id not in tracks_history:
                    tracks_history[track_id] = []
                    track_frames[track_id] = []
                tracks_history[track_id].append(box)
                track_frames[track_id].append(frame_idx)

        # Build SubjectTrack objects
        subject_tracks = []
        for tid, boxes in tracks_history.items():
            if len(boxes) < 2:
                continue  # Ignore single-frame tracks
            frames_list = track_frames[tid]
            center_x = sum((b.x + b.width / 2) for b in boxes) / len(boxes)
            center_y = sum((b.y + b.height / 2) for b in boxes) / len(boxes)
            subject_tracks.append(SubjectTrack(
                track_id=tid,
                face_boxes=boxes,
                start_frame=frames_list[0],
                end_frame=frames_list[-1],
                center_x=center_x,
                center_y=center_y,
            ))

        logger.info(f"SimpleSubjectTracker: {len(subject_tracks)} tracks created from {len(face_boxes_per_frame)} frames.")
        return subject_tracks

    def _match_boxes(self, boxes: list[FaceBox]) -> list[tuple[FaceBox, int | None]]:
        """Match current boxes to active tracks using IoU."""
        if not self._active_tracks:
            return [(b, None) for b in boxes]

        track_ids = list(self._active_tracks.keys())
        track_boxes = [t["boxes"][-1] for t in self._active_tracks.values()]

        # Compute IoU matrix
        iou_matrix = np.zeros((len(boxes), len(track_boxes)))
        for i, box in enumerate(boxes):
            for j, tbox in enumerate(track_boxes):
                iou_matrix[i, j] = self._compute_iou(box, tbox)

        # Greedy matching (can be replaced with Hungarian via scipy)
        matched = []
        used_tracks = set()
        for i in range(len(boxes)):
            best_j = int(np.argmax(iou_matrix[i]))
            best_iou = iou_matrix[i, best_j]
            if best_iou >= self.iou_threshold and best_j not in used_tracks:
                matched.append((boxes[i], track_ids[best_j]))
                used_tracks.add(best_j)
                iou_matrix[:, best_j] = -1.0  # Prevent reuse
            else:
                matched.append((boxes[i], None))

        return matched

    @staticmethod
    def _compute_iou(a: FaceBox, b: FaceBox) -> float:
        """Compute Intersection over Union of two FaceBoxes."""
        x1 = max(a.x, b.x)
        y1 = max(a.y, b.y)
        x2 = min(a.x + a.width, b.x + b.width)
        y2 = min(a.y + a.height, b.y + b.height)

        inter_w = max(0, x2 - x1)
        inter_h = max(0, y2 - y1)
        inter = inter_w * inter_h

        area_a = a.width * a.height
        area_b = b.width * b.height
        union = area_a + area_b - inter

        return inter / union if union > 0 else 0.0
