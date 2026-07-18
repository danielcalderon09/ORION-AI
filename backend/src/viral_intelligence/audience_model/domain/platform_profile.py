"""Audience Model interfaces and domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class PlatformProfile:
    """Behavioral profile for a specific platform."""
    platform_id: str  # tiktok, youtube_shorts, facebook_reels, instagram_reels
    optimal_duration_range: tuple[float, float]  # (min_sec, max_sec)
    ideal_hook_duration: float
    preferred_aspect_ratio: str  # "9:16", "1:1", etc.
    sound_importance: float  # 0-1
    caption_style: str  # "animated", "static", "minimal"
    pacing_preference: str  # "fast", "moderate", "slow"
    peak_engagement_times: list[str] | None  # optional
    attention_span_median: float  # seconds
    shares_vs_views_ratio: float  # approximate


@dataclass
class AudienceBrief:
    """Brief generated for the Audience Director."""
    target_platform: str
    platform_profile: PlatformProfile
    estimated_attention_span: float
    optimal_clip_count: int
    editing_constraints: dict[str, Any]
    content_recommendations: list[str]


class IAudienceModel(Protocol):
    """Model of audience behavior per platform."""
    def get_platform_profile(self, platform_id: str) -> PlatformProfile: ...
    def estimate_attention_span(self, content_features: dict[str, Any], platform_id: str) -> float: ...
    def recommend_clip_count(self, video_duration: float, platform_id: str) -> int: ...
    def adapt_pacing(self, base_pacing: str, platform_id: str) -> str: ...
    def generate_brief(self, content_features: dict[str, Any], platform_id: str) -> AudienceBrief: ...
