"""Creative Memory implementation."""

import json
from pathlib import Path

from backend.src.sprint4.creative_memory.domain.creative_pattern import (
    CreativePattern, ICreativeMemory,
)
from backend.src.infrastructure.config.settings import settings


class FileSystemCreativeMemory(ICreativeMemory):
    """Stores and retrieves creative patterns on filesystem."""

    def __init__(self):
        self.memory_dir = settings.ORION_HOME / "creative_memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._patterns: dict[str, CreativePattern] = {}
        self.load()

    def store_pattern(self, pattern: CreativePattern) -> None:
        self._patterns[pattern.pattern_id] = pattern
        self.persist()

    def find_patterns(self, category: str, platform: str, decision_type: str | None = None) -> list[CreativePattern]:
        results = []
        for p in self._patterns.values():
            if p.category == category and p.platform == platform:
                if decision_type is None or p.decision_type == decision_type:
                    results.append(p)
        # Sort by success rate descending
        results.sort(key=lambda x: x.success_rate, reverse=True)
        return results

    def get_best_practice(self, category: str, platform: str, decision_type: str) -> CreativePattern | None:
        patterns = self.find_patterns(category, platform, decision_type)
        return patterns[0] if patterns else None

    def update_outcome(self, pattern_id: str, outcome: dict) -> None:
        if pattern_id not in self._patterns:
            return
        pattern = self._patterns[pattern_id]
        # Bayesian-like update of success rate
        old_success = pattern.success_rate
        new_rating = outcome.get("user_rating", 3) / 5.0
        usage = pattern.usage_count + 1
        new_success = (old_success * (usage - 1) + new_rating) / usage
        pattern.success_rate = new_success
        pattern.usage_count = usage
        pattern.outcome = {**pattern.outcome, **outcome}
        self._patterns[pattern_id] = pattern
        self.persist()

    def persist(self) -> None:
        data = {pid: self._pattern_to_dict(p) for pid, p in self._patterns.items()}
        with open(self.memory_dir / "patterns.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load(self) -> None:
        path = self.memory_dir / "patterns.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._patterns = {pid: self._dict_to_pattern(p) for pid, p in data.items()}

    def _pattern_to_dict(self, p: CreativePattern) -> dict:
        return {
            "pattern_id": p.pattern_id,
            "category": p.category,
            "platform": p.platform,
            "decision_type": p.decision_type,
            "context_features": p.context_features,
            "action": p.action,
            "outcome": p.outcome,
            "usage_count": p.usage_count,
            "success_rate": p.success_rate,
        }

    def _dict_to_pattern(self, d: dict) -> CreativePattern:
        return CreativePattern(
            pattern_id=d["pattern_id"],
            category=d["category"],
            platform=d["platform"],
            decision_type=d["decision_type"],
            context_features=d.get("context_features", {}),
            action=d.get("action", {}),
            outcome=d.get("outcome", {}),
            usage_count=d.get("usage_count", 0),
            success_rate=d.get("success_rate", 0.5),
        )
