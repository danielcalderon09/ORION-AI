"""Pipeline Cache with content-addressable storage."""

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.src.infrastructure.config.settings import settings


class PipelineCache:
    """Content-addressable cache for pipeline stage results."""

    def __init__(self, max_size_mb: int = 2048, ttl_hours: int = 168):
        self.cache_dir = settings.ORION_HOME / "pipeline_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_mb = max_size_mb
        self.ttl_hours = ttl_hours
        self._index_path = self.cache_dir / "index.json"
        self._index: dict[str, dict] = self._load_index()

    def _compute_key(self, file_hash: str, stage_name: str, config_version: str) -> str:
        """Compute a cache key from file hash + stage + config."""
        raw = f"{file_hash}:{stage_name}:{config_version}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _load_index(self) -> dict[str, dict]:
        if self._index_path.exists():
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, default=str)

    def get(self, file_hash: str, stage_name: str, config_version: str) -> dict[str, Any] | None:
        """Retrieve cached result if available and not expired."""
        key = self._compute_key(file_hash, stage_name, config_version)
        entry = self._index.get(key)
        if entry is None:
            return None

        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            self._index.pop(key, None)
            self._save_index()
            return None

        # Check TTL
        from datetime import datetime, timedelta
        created = datetime.fromisoformat(entry["created"])
        if datetime.utcnow() - created > timedelta(hours=self.ttl_hours):
            cache_file.unlink(missing_ok=True)
            self._index.pop(key, None)
            self._save_index()
            return None

        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def put(self, file_hash: str, stage_name: str, config_version: str, data: dict[str, Any]) -> None:
        """Store a stage result in the cache."""
        key = self._compute_key(file_hash, stage_name, config_version)
        cache_file = self.cache_dir / f"{key}.json"

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)

        from datetime import datetime
        self._index[key] = {
            "stage": stage_name,
            "file_hash_prefix": file_hash[:16],
            "config_version": config_version,
            "created": datetime.utcnow().isoformat(),
            "size_kb": cache_file.stat().st_size / 1024,
        }
        self._save_index()
        self._enforce_size_limit()

    def _enforce_size_limit(self) -> None:
        """Evict oldest entries if total size exceeds limit."""
        total_mb = sum(
            (self.cache_dir / f"{k}.json").stat().st_size / (1024 * 1024)
            for k in self._index if (self.cache_dir / f"{k}.json").exists()
        )
        if total_mb <= self.max_size_mb:
            return

        # LRU eviction
        sorted_entries = sorted(self._index.items(), key=lambda x: x[1].get("created", ""))
        while total_mb > self.max_size_mb and sorted_entries:
            oldest_key, _ = sorted_entries.pop(0)
            cache_file = self.cache_dir / f"{oldest_key}.json"
            if cache_file.exists():
                size_mb = cache_file.stat().st_size / (1024 * 1024)
                cache_file.unlink()
                total_mb -= size_mb
            self._index.pop(oldest_key, None)

        self._save_index()

    def invalidate(self, file_hash: str | None = None, stage_name: str | None = None) -> int:
        """Invalidate cache entries. Returns count removed."""
        removed = 0
        keys_to_remove = []
        for key, entry in list(self._index.items()):
            if file_hash and not entry.get("file_hash_prefix", "").startswith(file_hash[:16]):
                continue
            if stage_name and entry.get("stage") != stage_name:
                continue
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                cache_file.unlink()
            keys_to_remove.append(key)
            removed += 1

        for k in keys_to_remove:
            self._index.pop(k, None)
        self._save_index()
        return removed

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total_entries = len(self._index)
        total_size_mb = sum(
            (self.cache_dir / f"{k}.json").stat().st_size / (1024 * 1024)
            for k in self._index if (self.cache_dir / f"{k}.json").exists()
        )
        return {
            "entries": total_entries,
            "total_size_mb": round(total_size_mb, 2),
            "max_size_mb": self.max_size_mb,
            "ttl_hours": self.ttl_hours,
            "hit_rate": None,  # Would need tracking
        }
