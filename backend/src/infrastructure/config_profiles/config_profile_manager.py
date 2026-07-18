"""Configuration profile manager with 7 built-in profiles."""
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class ProfileConfig:
    name: str
    description: str
    num_variants: int
    confidence_threshold: float
    enable_multimodal: bool
    enable_audio: bool
    enable_speech: bool
    enable_semantic: bool
    enable_viral: bool
    enable_candidates: bool
    num_candidates: int
    quality_level: str
    speed_priority: bool


class ConfigProfileManager:
    """Manages 7 built-in profiles: fast, balanced, quality, gaming, podcast, sports, anime."""

    _PROFILES: Dict[str, ProfileConfig] = {
        "fast": ProfileConfig(
            name="fast",
            description="Speed-first processing. Reduced quality, no candidates.",
            num_variants=1,
            confidence_threshold=0.5,
            enable_multimodal=False,
            enable_audio=True,
            enable_speech=False,
            enable_semantic=False,
            enable_viral=False,
            enable_candidates=False,
            num_candidates=1,
            quality_level="low",
            speed_priority=True,
        ),
        "balanced": ProfileConfig(
            name="balanced",
            description="Balanced quality and speed.",
            num_variants=3,
            confidence_threshold=0.7,
            enable_multimodal=True,
            enable_audio=True,
            enable_speech=True,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=3,
            quality_level="medium",
            speed_priority=False,
        ),
        "quality": ProfileConfig(
            name="quality",
            description="Maximum quality with all features enabled.",
            num_variants=5,
            confidence_threshold=0.8,
            enable_multimodal=True,
            enable_audio=True,
            enable_speech=True,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=5,
            quality_level="high",
            speed_priority=False,
        ),
        "gaming": ProfileConfig(
            name="gaming",
            description="Optimized for gaming videos: action detection, fast cuts, audio focus.",
            num_variants=3,
            confidence_threshold=0.65,
            enable_multimodal=True,
            enable_audio=True,
            enable_speech=False,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=3,
            quality_level="medium",
            speed_priority=False,
        ),
        "podcast": ProfileConfig(
            name="podcast",
            description="Optimized for long-form talk: speech transcription, narrative focus.",
            num_variants=2,
            confidence_threshold=0.75,
            enable_multimodal=False,
            enable_audio=True,
            enable_speech=True,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=2,
            quality_level="medium",
            speed_priority=False,
        ),
        "sports": ProfileConfig(
            name="sports",
            description="Optimized for sports: fast motion, highlight detection, crowd audio.",
            num_variants=3,
            confidence_threshold=0.6,
            enable_multimodal=True,
            enable_audio=True,
            enable_speech=False,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=3,
            quality_level="medium",
            speed_priority=False,
        ),
        "anime": ProfileConfig(
            name="anime",
            description="Optimized for anime: scene change detection, subtitle awareness, art style.",
            num_variants=3,
            confidence_threshold=0.7,
            enable_multimodal=True,
            enable_audio=True,
            enable_speech=False,
            enable_semantic=True,
            enable_viral=True,
            enable_candidates=True,
            num_candidates=3,
            quality_level="medium",
            speed_priority=False,
        ),
    }

    def __init__(self) -> None:
        self._active_profile: str = "balanced"

    def get_profile(self, name: str | None = None) -> ProfileConfig:
        profile_name = name or self._active_profile
        if profile_name not in self._PROFILES:
            raise ValueError(f"Unknown profile '{profile_name}'")
        return self._PROFILES[profile_name]

    def set_active_profile(self, name: str) -> None:
        if name not in self._PROFILES:
            raise ValueError(f"Unknown profile '{name}'")
        self._active_profile = name

    def list_profiles(self) -> Dict[str, str]:
        return {k: v.description for k, v in self._PROFILES.items()}
