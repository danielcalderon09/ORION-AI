"""Checkpoint and Recovery system for fault tolerance."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from backend.src.infrastructure.config.settings import settings


@dataclass
class Checkpoint:
    """A saved state of the pipeline at a specific stage."""
    checkpoint_id: str
    project_id: str
    stage_name: str
    stage_index: int
    timestamp: datetime
    project_brain_state: dict[str, Any]
    feature_paths: dict[str, str]  # agent_id -> feature file path
    status: str  # "completed", "failed", "in_progress"


class CheckpointManager:
    """Manages pipeline checkpoints for crash recovery."""

    def __init__(self):
        self.checkpoint_dir = settings.ORION_HOME / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, project_id: UUID, stage_name: str, stage_index: int,
                        brain_state: dict[str, Any], feature_paths: dict[str, str]) -> Path:
        """Save a checkpoint after a stage completes."""
        cp = Checkpoint(
            checkpoint_id=f"{project_id}_{stage_name}_{int(datetime.utcnow().timestamp())}",
            project_id=str(project_id),
            stage_name=stage_name,
            stage_index=stage_index,
            timestamp=datetime.utcnow(),
            project_brain_state=brain_state,
            feature_paths=feature_paths,
            status="completed",
        )

        path = self.checkpoint_dir / f"{project_id}_{stage_index:02d}_{stage_name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "checkpoint_id": cp.checkpoint_id,
                "project_id": cp.project_id,
                "stage_name": cp.stage_name,
                "stage_index": cp.stage_index,
                "timestamp": cp.timestamp.isoformat(),
                "brain_state": cp.project_brain_state,
                "feature_paths": cp.feature_paths,
                "status": cp.status,
            }, f, indent=2, default=str)
        return path

    def get_latest_checkpoint(self, project_id: UUID) -> Checkpoint | None:
        """Find the most recent successful checkpoint for a project."""
        pattern = f"{project_id}_*.json"
        checkpoints = sorted(self.checkpoint_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

        for cp_path in checkpoints:
            with open(cp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") == "completed":
                return Checkpoint(
                    checkpoint_id=data["checkpoint_id"],
                    project_id=data["project_id"],
                    stage_name=data["stage_name"],
                    stage_index=data["stage_index"],
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    project_brain_state=data.get("brain_state", {}),
                    feature_paths=data.get("feature_paths", {}),
                    status=data["status"],
                )
        return None

    def get_recovery_point(self, project_id: UUID) -> tuple[str, int, dict[str, Any]] | None:
        """Get the stage name, index, and brain state to resume from."""
        cp = self.get_latest_checkpoint(project_id)
        if cp is None:
            return None
        return (cp.stage_name, cp.stage_index, cp.project_brain_state)

    def list_checkpoints(self, project_id: UUID) -> list[dict]:
        """List all checkpoints for a project."""
        pattern = f"{project_id}_*.json"
        results = []
        for cp_path in sorted(self.checkpoint_dir.glob(pattern), key=lambda p: p.stat().st_mtime):
            with open(cp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append({
                "stage": data.get("stage_name"),
                "index": data.get("stage_index"),
                "status": data.get("status"),
                "timestamp": data.get("timestamp"),
            })
        return results

    def clean_old_checkpoints(self, project_id: UUID, keep_last: int = 3) -> None:
        """Keep only the N most recent checkpoints for a project."""
        pattern = f"{project_id}_*.json"
        checkpoints = sorted(self.checkpoint_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in checkpoints[keep_last:]:
            old.unlink()
