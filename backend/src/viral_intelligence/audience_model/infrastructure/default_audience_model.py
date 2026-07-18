"""Audience Model implementation."""

from backend.src.viral_intelligence.audience_model.domain.platform_profile import (
    AudienceBrief, IAudienceModel, PlatformProfile,
)


class DefaultAudienceModel(IAudienceModel):
    """Default audience behavior model with platform-specific profiles."""

    def __init__(self):
        self._profiles = {
            "tiktok": PlatformProfile(
                platform_id="tiktok",
                optimal_duration_range=(15.0, 60.0),
                ideal_hook_duration=1.5,
                preferred_aspect_ratio="9:16",
                sound_importance=0.9,
                caption_style="animated",
                pacing_preference="fast",
                peak_engagement_times=None,
                attention_span_median=8.0,
                shares_vs_views_ratio=0.08,
            ),
            "youtube_shorts": PlatformProfile(
                platform_id="youtube_shorts",
                optimal_duration_range=(20.0, 60.0),
                ideal_hook_duration=2.0,
                preferred_aspect_ratio="9:16",
                sound_importance=0.8,
                caption_style="static",
                pacing_preference="moderate",
                peak_engagement_times=None,
                attention_span_median=15.0,
                shares_vs_views_ratio=0.04,
            ),
            "facebook_reels": PlatformProfile(
                platform_id="facebook_reels",
                optimal_duration_range=(15.0, 45.0),
                ideal_hook_duration=2.0,
                preferred_aspect_ratio="9:16",
                sound_importance=0.7,
                caption_style="static",
                pacing_preference="moderate",
                peak_engagement_times=None,
                attention_span_median=10.0,
                shares_vs_views_ratio=0.06,
            ),
            "instagram_reels": PlatformProfile(
                platform_id="instagram_reels",
                optimal_duration_range=(15.0, 60.0),
                ideal_hook_duration=1.5,
                preferred_aspect_ratio="9:16",
                sound_importance=0.85,
                caption_style="animated",
                pacing_preference="fast",
                peak_engagement_times=None,
                attention_span_median=9.0,
                shares_vs_views_ratio=0.05,
            ),
        }

    def get_platform_profile(self, platform_id: str) -> PlatformProfile:
        return self._profiles.get(platform_id, self._profiles["tiktok"])

    def estimate_attention_span(self, content_features: dict, platform_id: str) -> float:
        profile = self.get_platform_profile(platform_id)
        base = profile.attention_span_median

        # Adjust based on content
        viral_score = content_features.get("viral_score_map", {}).get("average_score", 0.5)
        if viral_score > 0.8:
            base *= 1.3  # high viral potential extends attention
        elif viral_score < 0.3:
            base *= 0.7

        # Shorter attention for fast-paced content
        scene_rate = content_features.get("vision_features", {}).get("scene_count", 0) / 60
        if scene_rate > 1.0:
            base *= 0.9

        return round(base, 1)

    def recommend_clip_count(self, video_duration: float, platform_id: str) -> int:
        profile = self.get_platform_profile(platform_id)
        min_dur, max_dur = profile.optimal_duration_range

        # Simple heuristic: split video into optimal-duration clips
        if video_duration <= max_dur:
            return 1

        target_per_clip = (min_dur + max_dur) / 2
        count = int(video_duration / target_per_clip)
        return max(1, min(count, 10))  # cap at 10 clips

    def adapt_pacing(self, base_pacing: str, platform_id: str) -> str:
        profile = self.get_platform_profile(platform_id)
        # Platform preference overrides base unless explicitly set
        if base_pacing == "auto":
            return profile.pacing_preference
        return base_pacing

    def generate_brief(self, content_features: dict, platform_id: str) -> AudienceBrief:
        profile = self.get_platform_profile(platform_id)
        attention_span = self.estimate_attention_span(content_features, platform_id)
        clip_count = self.recommend_clip_count(
            content_features.get("vision_features", {}).get("duration_seconds", 60),
            platform_id,
        )

        # Generate constraints
        constraints = {
            "max_clip_duration": profile.optimal_duration_range[1],
            "min_clip_duration": profile.optimal_duration_range[0],
            "hook_duration": profile.ideal_hook_duration,
            "aspect_ratio": profile.preferred_aspect_ratio,
            "sound_on": profile.sound_importance > 0.7,
            "caption_style": profile.caption_style,
            "pacing": profile.pacing_preference,
        }

        # Content recommendations
        recommendations = []
        if profile.platform_id == "tiktok":
            recommendations.append("Start with the most surprising moment within 1 second")
            recommendations.append("Use trending sounds or audio hooks")
            recommendations.append("Add text overlay for sound-off viewers")
        elif profile.platform_id == "youtube_shorts":
            recommendations.append("Tell a mini-story with setup and payoff")
            recommendations.append("Use loop-friendly endings")
            recommendations.append("Engage viewer curiosity early")
        elif profile.platform_id == "facebook_reels":
            recommendations.append("Emphasize emotional reactions")
            recommendations.append("Use bold captions for sound-off browsing")
        elif profile.platform_id == "instagram_reels":
            recommendations.append("Aesthetic visuals in first frame")
            recommendations.append("Use Instagram-native trending audio")

        return AudienceBrief(
            target_platform=platform_id,
            platform_profile=profile,
            estimated_attention_span=attention_span,
            optimal_clip_count=clip_count,
            editing_constraints=constraints,
            content_recommendations=recommendations,
        )
