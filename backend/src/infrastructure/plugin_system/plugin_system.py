"""Plugin system with manifest-based discovery."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PluginManifest:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        with open(manifest_path, "r", encoding="utf-8") as f:
            self.data: Dict[str, Any] = json.load(f)

    @property
    def name(self) -> str:
        return self.data.get("name", "unknown")

    @property
    def version(self) -> str:
        return self.data.get("version", "0.0.0")

    @property
    def entry_point(self) -> str:
        return self.data.get("entry_point", "")


class PluginSystem:
    """Discovers and registers plugins from ~/.orion/plugins/."""

    def __init__(self, plugins_dir: Optional[Path] = None) -> None:
        self.plugins_dir = plugins_dir or (Path.home() / ".orion" / "plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: List[PluginManifest] = []

    def discover_plugins(self) -> List[PluginManifest]:
        self._plugins.clear()
        for subdir in self.plugins_dir.iterdir():
            manifest = subdir / "manifest.json"
            if manifest.exists():
                try:
                    self._plugins.append(PluginManifest(manifest))
                except Exception:
                    continue
        return list(self._plugins)

    def list_plugins(self) -> List[Dict[str, str]]:
        return [{"name": p.name, "version": p.version, "entry_point": p.entry_point} for p in self._plugins]
