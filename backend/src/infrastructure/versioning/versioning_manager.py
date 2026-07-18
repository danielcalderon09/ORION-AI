"""Versioning and reproducibility manager."""
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
from pathlib import Path


@dataclass
class ReproducibilityManifest:
    video_path: Path
    pipeline_version: str
    target_platform: str
    component_versions: Dict[str, str] = field(default_factory=dict)
    config_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_path": str(self.video_path),
            "pipeline_version": self.pipeline_version,
            "target_platform": self.target_platform,
            "component_versions": self.component_versions,
            "config_hash": self.config_hash,
        }


class VersioningManager:
    """Tracks component versions and produces reproducibility manifests."""

    def __init__(self) -> None:
        self._versions: Dict[str, str] = {}

    def auto_detect_versions(self) -> None:
        self._versions["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self._versions["orion"] = "5.0.0"

    def register_component(self, name: str, version: str) -> None:
        self._versions[name] = version

    def get_component_versions(self) -> Dict[str, str]:
        return dict(self._versions)

    def create_reproducibility_manifest(self, video_path: Path, pipeline_version: str, target_platform: str) -> ReproducibilityManifest:
        return ReproducibilityManifest(
            video_path=video_path,
            pipeline_version=pipeline_version,
            target_platform=target_platform,
            component_versions=dict(self._versions),
        )
