"""Application settings and configuration."""

from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Orion AI application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # App
    APP_NAME: str = "Orion AI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ORION_PROMPT_VIDEO_ENABLED: bool = False

    # Database
    ORION_DATABASE_URL: str | None = None
    ORION_DATABASE_ECHO: bool = False
    ORION_PRODUCTION_AUTO_MIGRATE: bool = False
    ORION_PRODUCTION_WORKER_ENABLED: bool = True
    ORION_PRODUCTION_WORKER_OWNER_ID: str | None = None
    ORION_PRODUCTION_POLL_INTERVAL_SECONDS: float = 0.5
    ORION_PRODUCTION_LEASE_DURATION_SECONDS: float = 30.0
    ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS: float = 10.0
    ORION_PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS: float = 10.0
    ORION_PRODUCTION_MAX_CYCLES: int | None = None
    ORION_PLANNING_PROVIDER: str = "simulated"
    ORION_PLANNING_MODEL: str = "openai/gpt-4.1-mini"
    ORION_PLANNING_API_KEY: SecretStr | None = None
    ORION_PLANNING_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_PLANNING_TIMEOUT_SECONDS: float = 30.0
    ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS: int = 2
    ORION_PLANNING_RETRY_BASE_DELAY_SECONDS: float = 0.25
    ORION_PLANNING_MAX_OUTPUT_TOKENS: int = 4096
    ORION_PLANNING_TEMPERATURE: float = 0.2
    ORION_PLANNING_RECONCILE_ARTIFACTS: bool = True
    ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS: float = 300.0
    ORION_PLANNING_ORPHAN_ACTION: Literal["delete", "quarantine"] = "quarantine"
    ORION_PLANNING_QUARANTINE_DIR: str = "production-quarantine"
    ORION_SCRIPTING_PROVIDER: Literal["simulated", "openrouter"] = "simulated"
    ORION_SCRIPTING_MODEL: str = ""
    ORION_SCRIPTING_API_KEY: SecretStr | None = None
    ORION_OPENROUTER_API_KEY: SecretStr | None = None
    ORION_SCRIPTING_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS: bool = False
    ORION_SCRIPTING_ESTIMATED_COST_USD: Decimal | None = None
    ORION_SCRIPTING_MAX_ESTIMATED_COST_USD: Decimal | None = None
    ORION_SCRIPTING_MAX_ESTIMATED_JOB_COST_USD: Decimal | None = None
    ORION_SCRIPTING_MAX_REQUESTS_PER_JOB: int = 2
    ORION_SCRIPTING_MAX_DURATION_POLICY_RETRIES: int = 1
    ORION_SCRIPTING_TIMEOUT_SECONDS: float = 120.0
    ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS: Literal[1] = 1
    ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS: float = 0.25
    ORION_SCRIPTING_MAX_OUTPUT_TOKENS: int = 8192
    ORION_SCRIPTING_TEMPERATURE: float = 0.2
    ORION_SCRIPTING_MAX_RESPONSE_BYTES: int = 2_000_000
    ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES: int = 2_000_000
    ORION_SCRIPTING_MAX_PLAN_BYTES: int = 1_000_000
    ORION_SCRIPTING_MAX_SCRIPT_BYTES: int = 2_000_000
    ORION_SCENE_PLANNING_PROVIDER: str = "simulated"
    ORION_SCENE_PLANNING_MODEL: str = "openai/gpt-4.1-mini"
    ORION_SCENE_PLANNING_API_KEY: SecretStr | None = None
    ORION_SCENE_PLANNING_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_SCENE_PLANNING_TIMEOUT_SECONDS: float = 30.0
    ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS: int = 2
    ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS: float = 0.25
    ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS: int = 8192
    ORION_SCENE_PLANNING_TEMPERATURE: float = 0.2
    ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES: int = 2_000_000
    ORION_SCENE_PLANNING_MAX_PLAN_BYTES: int = 4_000_000
    ORION_VISUAL_ASSET_PLANNING_PROVIDER: str = "simulated"
    ORION_VISUAL_ASSET_PLANNING_MODEL: str = "openai/gpt-4.1-mini"
    ORION_VISUAL_ASSET_PLANNING_API_KEY: SecretStr | None = None
    ORION_VISUAL_ASSET_PLANNING_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS: float = 30.0
    ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS: int = 2
    ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS: float = 0.25
    ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS: int = 12_000
    ORION_VISUAL_ASSET_PLANNING_TEMPERATURE: float = 0.2
    ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES: int = 4_000_000
    ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES: int = 8_000_000
    ORION_VISUAL_STRATEGY: Literal[
        "full_video", "hybrid_balanced", "hybrid_economy", "image_only"
    ] = "full_video"
    ORION_HYBRID_IMAGE_ESTIMATED_COST_USD: Decimal = Decimal("0.04")
    ORION_HYBRID_VIDEO_PRICE_PER_SECOND_USD: Decimal = Decimal("0.03")
    ORION_MAX_TOTAL_VISUAL_COST_USD: Decimal = Decimal("2.50")
    ORION_BINARY_ASSET_MAX_SIZE_BYTES: int = 25_000_000
    ORION_BINARY_ASSET_ALLOWED_MIME_TYPES: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
    )
    ORION_BINARY_ASSET_ALLOWED_EXTENSIONS: tuple[str, ...] = (
        "png",
        "jpg",
        "jpeg",
        "webp",
    )
    ORION_IMAGE_ACQUISITION_PROVIDER: str = "simulated"
    ORION_IMAGE_ACQUISITION_MODEL: str = "google/gemini-3.1-flash-lite-image"
    ORION_IMAGE_ACQUISITION_API_KEY: SecretStr | None = None
    ORION_IMAGE_ACQUISITION_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS: float = 120.0
    ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS: Literal[1] = 1
    ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS: float = 1.0
    ORION_IMAGE_ACQUISITION_OUTPUT_FORMAT: Literal["png", "jpeg", "webp"] = "png"
    ORION_IMAGE_ACQUISITION_QUALITY: Literal["auto", "low", "medium", "high"] = "auto"
    ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES: int = 40_000_000
    ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES: int = 25_000_000
    ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES: int = 8_000_000
    ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_IMAGE_ACQUISITION_PROVIDER_ONLY: str | None = None
    ORION_IMAGE_ACQUISITION_ALLOW_BILLABLE_REQUESTS: bool = False
    ORION_IMAGE_ACQUISITION_ESTIMATED_COST_USD: Decimal | None = None
    ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD: Decimal | None = None
    ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB: int = 1
    ORION_IMAGE_ACQUISITION_MAX_REQUEST_RECORD_BYTES: int = 1_000_000
    ORION_VIDEO_CLIP_GENERATION_PROVIDER: Literal["simulated", "openrouter"] = "simulated"
    ORION_VIDEO_CLIP_GENERATION_MODEL: str = "simulated-video-v1"
    ORION_VIDEO_CLIP_GENERATION_OUTPUT_FORMAT: Literal["mp4"] = "mp4"
    ORION_VIDEO_CLIP_GENERATION_CODEC: Literal["h264"] = "h264"
    ORION_VIDEO_CLIP_GENERATION_RESOLUTION: Literal["720p", "1080p"] = "720p"
    ORION_VIDEO_CLIP_GENERATION_GENERATE_AUDIO: Literal[False] = False
    ORION_VIDEO_CLIP_GENERATION_FRAME_RATE: Literal[24, 30] = 24
    ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS: float = 4
    ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS: float = 10
    ORION_VIDEO_CLIP_GENERATION_MAX_SOURCE_MANIFEST_BYTES: int = 4_000_000
    ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES: int = 50_000_000
    ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH: str | None = None
    ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH: str | None = None
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY: SecretStr | None = None
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_TIMEOUT_SECONDS: float = 30
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_POLL_INTERVAL_SECONDS: float = 5
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_SECONDS: float = 900
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_ATTEMPTS: int = 180
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_RESPONSE_BYTES: int = 2_000_000
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_VIDEO_BYTES: int = 50_000_000
    ORION_VIDEO_CLIP_GENERATION_OPENROUTER_CAPABILITY_CACHE_TTL_SECONDS: float = 3600
    ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD: Decimal = Decimal("1.00")
    ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD: Decimal = Decimal("1.00")
    ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB: int = 1
    ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS: bool = False
    ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER: Literal["disabled", "filesystem"] = "disabled"
    ORION_ASSET_PUBLISHING_PUBLISHER: Literal["null", "filesystem"] = "null"
    ORION_ASSET_PUBLISHING_PUBLIC_ROOT: Path | None = None
    ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL: str = "https://assets.orion.test"
    ORION_ASSET_PUBLISHING_LIFETIME_SECONDS: int = 900
    ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES: int = 250_000_000
    ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_SPEECH_GENERATION_PROVIDER: Literal["simulated", "openrouter"] = "simulated"
    ORION_SPEECH_GENERATION_VOICE: str = "simulated-neutral-v1"
    ORION_SPEECH_GENERATION_LANGUAGE: str = "es-ES"
    ORION_SPEECH_GENERATION_WORDS_PER_MINUTE: int = 150
    ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ: int = 24_000
    ORION_SPEECH_GENERATION_CHANNEL_COUNT: Literal[1] = 1
    ORION_SPEECH_GENERATION_SAMPLE_WIDTH_BYTES: Literal[2] = 2
    ORION_SPEECH_GENERATION_MIN_DURATION_MS: int = 250
    ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS: int = 120_000
    ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES: int = 8_000_000
    ORION_SPEECH_GENERATION_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_SPEECH_GENERATION_MAX_SCRIPT_BYTES: int = 2_000_000
    ORION_SPEECH_GENERATION_GENERATING_STALE_AFTER_SECONDS: float = 30
    ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS: bool = False
    ORION_SPEECH_GENERATION_REMOTE_PROVIDER: Literal["disabled", "openrouter"] = "disabled"
    ORION_SPEECH_GENERATION_REMOTE_MODEL: str | None = None
    ORION_SPEECH_GENERATION_REMOTE_VOICE: str | None = None
    ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST: Decimal | None = None
    ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST: Decimal | None = None
    ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB: int = 1
    ORION_SPEECH_GENERATION_OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_SPEECH_GENERATION_OPENROUTER_TIMEOUT_SECONDS: float = 120
    ORION_SPEECH_GENERATION_REMOTE_MAX_POLL_ATTEMPTS: int = 120
    ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS: float = 5
    ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES: int = 1_000_000
    ORION_NARRATION_FITTING_PROVIDER: Literal["disabled", "openrouter"] = "disabled"
    ORION_NARRATION_FITTING_MODEL: str = "google/gemini-2.5-flash-lite"
    ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS: bool = False
    ORION_NARRATION_FITTING_MAX_ATTEMPTS: int = 2
    ORION_NARRATION_FITTING_MAX_PROVIDER_RETRIES: int = 1
    ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT: Decimal | None = None
    ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT: Decimal | None = None
    ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD: Decimal | None = None
    ORION_NARRATION_FITTING_OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_NARRATION_FITTING_TIMEOUT_SECONDS: float = 60
    ORION_NARRATION_FITTING_MAX_TRANSPORT_ATTEMPTS: Literal[1] = 1
    ORION_NARRATION_FITTING_MAX_OUTPUT_TOKENS: int = 512
    ORION_NARRATION_FITTING_TEMPERATURE: float = 0.1
    ORION_NARRATION_FITTING_MAX_RESPONSE_BYTES: int = 200_000
    ORION_MUSIC_GENERATION_PROVIDER: Literal["simulated"] = "simulated"
    ORION_MUSIC_GENERATION_MODEL: str = "google/lyria-3-clip-preview"
    ORION_VIDEO_FUTURE_PRIMARY_MODEL: str = "google/veo-3.1-lite"
    ORION_VIDEO_FUTURE_FAST_MODEL: str = "bytedance/seedance-2.0-fast"
    ORION_VIDEO_FUTURE_ALTERNATIVE_MODEL: str = "bytedance/seedance-2.0"
    ORION_SOUND_EFFECT_GENERATION_PROVIDER: Literal["simulated"] = "simulated"
    ORION_AUDIO_DESIGN_SAMPLE_RATE_HZ: Literal[24_000] = 24_000
    ORION_AUDIO_DESIGN_CHANNEL_COUNT: Literal[1] = 1
    ORION_AUDIO_DESIGN_SAMPLE_WIDTH_BYTES: Literal[2] = 2
    ORION_AUDIO_DESIGN_MIN_MUSIC_DURATION_MS: int = 1_000
    ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS: int = 180_000
    ORION_AUDIO_DESIGN_MIN_SOUND_EFFECT_DURATION_MS: int = 50
    ORION_AUDIO_DESIGN_MAX_SOUND_EFFECT_DURATION_MS: int = 5_000
    ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES: int = 10_000_000
    ORION_AUDIO_DESIGN_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_AUDIO_DESIGN_MAX_SCRIPT_BYTES: int = 2_000_000
    ORION_AUDIO_DESIGN_GENERATING_STALE_AFTER_SECONDS: float = 30
    ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES: int = 4_000_000
    ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES: int = 4_000_000
    ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_MEDIA_COMPOSITION_MAXIMUM_ABSOLUTE_EXTENSION_MS: int = 3_000
    ORION_MEDIA_COMPOSITION_MAXIMUM_RELATIVE_EXTENSION_RATIO: Decimal = Decimal("0.20")
    ORION_RENDERER: Literal["dry_run", "ffmpeg"] = "dry_run"
    ORION_FFMPEG_PATH: str | None = None
    ORION_FFPROBE_PATH: str | None = None
    ORION_RENDER_OUTPUT_CONTAINER: Literal["mp4"] = "mp4"
    ORION_RENDER_VIDEO_CODEC: Literal["h264"] = "h264"
    ORION_RENDER_AUDIO_CODEC: Literal["aac"] = "aac"
    ORION_RENDER_PIXEL_FORMAT: Literal["yuv420p"] = "yuv420p"
    ORION_RENDER_VIDEO_PRESET: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"
    ORION_RENDER_VIDEO_CRF: int = 20
    ORION_RENDER_AUDIO_BITRATE: Literal["96k", "128k", "160k", "192k", "256k", "320k"] = "192k"
    ORION_RENDER_PROCESS_TIMEOUT_SECONDS: int = 1800
    ORION_RENDER_PROBE_TIMEOUT_SECONDS: int = 30
    ORION_RENDER_MAX_STDERR_BYTES: int = 1_000_000
    ORION_RENDER_MAX_OUTPUT_BYTES: int = 2_000_000_000
    ORION_RENDER_DURATION_TOLERANCE_MS: int = 500
    ORION_RENDER_FRAME_RATE_TOLERANCE: float = 0.01
    ORION_RENDER_MAX_REQUEST_BYTES: int = 4_000_000
    ORION_RENDER_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_RENDER_MAX_EXECUTION_PLAN_BYTES: int = 4_000_000
    ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES: int = 4_000_000
    ORION_OPENROUTER_HTTP_REFERER: str | None = None
    ORION_OPENROUTER_APP_TITLE: str | None = None

    # Paths
    ORION_HOME: Path = Path.home() / ".orion"
    MODELS_DIR: Path = Path.home() / ".orion" / "models"
    PROJECTS_DIR: Path = Path.home() / "OrionProjects"
    TEMP_DIR: Path = Path.home() / ".orion" / "temp"

    # API
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    API_WORKERS: int = 1

    # Media
    DEFAULT_FPS: float = 1.0
    DEFAULT_AUDIO_SAMPLE_RATE: int = 16000
    TARGET_RESOLUTION_WIDTH: int = 1080
    TARGET_RESOLUTION_HEIGHT: int = 1920
    TARGET_VIDEO_CODEC: str = "libx264"
    TARGET_AUDIO_CODEC: str = "aac"
    TARGET_CONTAINER: str = "mp4"

    # Processing
    MAX_VIDEO_DURATION_SECONDS: float = 7200  # 2 hours for MVP
    SCENE_CHANGE_THRESHOLD: float = 0.3
    AUDIO_ENERGY_THRESHOLD: float = 0.1
    MIN_CLIP_DURATION_SECONDS: float = 5.0
    MAX_CLIP_DURATION_SECONDS: float = 60.0

    # Whisper
    WHISPER_MODEL_SIZE: str = "tiny"
    WHISPER_DEVICE: str = "auto"  # auto, cpu, cuda
    WHISPER_COMPUTE_TYPE: str = "int8"

    # GPU
    GPU_ENABLED: bool = True
    GPU_MEMORY_LIMIT_MB: int = 4096

    # Telemetry
    TELEMETRY_ENABLED: bool = True
    BENCHMARK_ENABLED: bool = True

    @field_validator(
        "ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH",
        "ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH",
        mode="before",
    )
    @classmethod
    def empty_video_executable_path_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("ORION_ASSET_PUBLISHING_PUBLIC_ROOT", mode="before")
    @classmethod
    def empty_asset_public_root_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "ORION_SPEECH_GENERATION_REMOTE_MODEL",
        "ORION_SPEECH_GENERATION_REMOTE_VOICE",
        "ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST",
        "ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST",
        mode="before",
    )
    @classmethod
    def empty_remote_speech_value_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "ORION_SCRIPTING_API_KEY",
        "ORION_OPENROUTER_API_KEY",
        "ORION_SCRIPTING_ESTIMATED_COST_USD",
        "ORION_SCRIPTING_MAX_ESTIMATED_COST_USD",
        mode="before",
    )
    @classmethod
    def empty_scripting_secret_or_cost_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "ORION_SCRIPTING_ESTIMATED_COST_USD",
        "ORION_SCRIPTING_MAX_ESTIMATED_COST_USD",
        "ORION_SCRIPTING_MAX_ESTIMATED_JOB_COST_USD",
        mode="before",
    )
    @classmethod
    def reject_float_scripting_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("OpenRouter scripting cost must not use float")
        return value

    @field_validator(
        "ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD",
        mode="before",
    )
    @classmethod
    def reject_float_video_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("OpenRouter video cost must not use float")
        return value

    @field_validator(
        "ORION_IMAGE_ACQUISITION_ESTIMATED_COST_USD",
        "ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD",
        mode="before",
    )
    @classmethod
    def empty_image_cost_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, float):
            raise ValueError("OpenRouter image cost must not use float")
        return value

    @field_validator(
        "ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST",
        "ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST",
        mode="before",
    )
    @classmethod
    def reject_float_speech_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("remote speech cost must not use float")
        return value

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Ensure directories exist
        self.ORION_HOME.mkdir(parents=True, exist_ok=True)
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    @model_validator(mode="after")
    def validate_production_runtime(self) -> "Settings":
        positive = {
            "ORION_PRODUCTION_POLL_INTERVAL_SECONDS": self.ORION_PRODUCTION_POLL_INTERVAL_SECONDS,
            "ORION_PRODUCTION_LEASE_DURATION_SECONDS": self.ORION_PRODUCTION_LEASE_DURATION_SECONDS,
            "ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS": self.ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS,
            "ORION_PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS": self.ORION_PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS,
            "ORION_PLANNING_TIMEOUT_SECONDS": self.ORION_PLANNING_TIMEOUT_SECONDS,
            "ORION_PLANNING_RETRY_BASE_DELAY_SECONDS": self.ORION_PLANNING_RETRY_BASE_DELAY_SECONDS,
            "ORION_SCRIPTING_TIMEOUT_SECONDS": self.ORION_SCRIPTING_TIMEOUT_SECONDS,
            "ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS": self.ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS,
            "ORION_SCENE_PLANNING_TIMEOUT_SECONDS": self.ORION_SCENE_PLANNING_TIMEOUT_SECONDS,
            "ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS": self.ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS,
            "ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS": self.ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS,
            "ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS": self.ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS,
            "ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS": self.ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS,
            "ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS": self.ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_TIMEOUT_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_TIMEOUT_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_POLL_INTERVAL_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_POLL_INTERVAL_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_SECONDS,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_CAPABILITY_CACHE_TTL_SECONDS": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_CAPABILITY_CACHE_TTL_SECONDS,
            "ORION_ASSET_PUBLISHING_LIFETIME_SECONDS": self.ORION_ASSET_PUBLISHING_LIFETIME_SECONDS,
            "ORION_SPEECH_GENERATION_GENERATING_STALE_AFTER_SECONDS": self.ORION_SPEECH_GENERATION_GENERATING_STALE_AFTER_SECONDS,
            "ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS": self.ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS,
            "ORION_AUDIO_DESIGN_GENERATING_STALE_AFTER_SECONDS": self.ORION_AUDIO_DESIGN_GENERATING_STALE_AFTER_SECONDS,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if (
            self.ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS
            >= self.ORION_PRODUCTION_LEASE_DURATION_SECONDS
        ):
            raise ValueError("production heartbeat interval must be shorter than lease duration")
        if self.ORION_PRODUCTION_MAX_CYCLES is not None and self.ORION_PRODUCTION_MAX_CYCLES < 1:
            raise ValueError("ORION_PRODUCTION_MAX_CYCLES must be positive")
        if not 1 <= self.ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError("ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS must be between 1 and 5")
        if not 1 <= self.ORION_PLANNING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_PLANNING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_PLANNING_TEMPERATURE <= 2:
            raise ValueError("ORION_PLANNING_TEMPERATURE must be between 0 and 2")
        if self.ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS != 1:
            raise ValueError("OpenRouter scripting does not permit automatic retries")
        if not 1 <= self.ORION_SCRIPTING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_SCRIPTING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_SCRIPTING_TEMPERATURE <= 2:
            raise ValueError("ORION_SCRIPTING_TEMPERATURE must be between 0 and 2")
        for scripting_cost in (
            self.ORION_SCRIPTING_ESTIMATED_COST_USD,
            self.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD,
        ):
            if scripting_cost is not None and scripting_cost <= 0:
                raise ValueError("OpenRouter scripting costs must be positive")
        if (
            self.ORION_SCRIPTING_ESTIMATED_COST_USD is not None
            and self.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD is not None
            and self.ORION_SCRIPTING_ESTIMATED_COST_USD
            > self.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD
        ):
            raise ValueError("OpenRouter scripting estimate exceeds authorization")
        if not 1 <= self.ORION_SCRIPTING_MAX_REQUESTS_PER_JOB <= 2:
            raise ValueError("ORION_SCRIPTING_MAX_REQUESTS_PER_JOB must be 1 or 2")
        if not 0 <= self.ORION_SCRIPTING_MAX_DURATION_POLICY_RETRIES <= 1:
            raise ValueError("ORION_SCRIPTING_MAX_DURATION_POLICY_RETRIES must be 0 or 1")
        if not 1 <= self.ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError("ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS must be between 1 and 5")
        if not 1 <= self.ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_SCENE_PLANNING_TEMPERATURE <= 2:
            raise ValueError("ORION_SCENE_PLANNING_TEMPERATURE must be between 0 and 2")
        if not 1 <= self.ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError(
                "ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS must be between 1 and 5"
            )
        if not 1 <= self.ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_VISUAL_ASSET_PLANNING_TEMPERATURE <= 2:
            raise ValueError("ORION_VISUAL_ASSET_PLANNING_TEMPERATURE must be between 0 and 2")
        for hybrid_name, hybrid_value in {
            "ORION_HYBRID_IMAGE_ESTIMATED_COST_USD": (
                self.ORION_HYBRID_IMAGE_ESTIMATED_COST_USD
            ),
            "ORION_HYBRID_VIDEO_PRICE_PER_SECOND_USD": (
                self.ORION_HYBRID_VIDEO_PRICE_PER_SECOND_USD
            ),
            "ORION_MAX_TOTAL_VISUAL_COST_USD": self.ORION_MAX_TOTAL_VISUAL_COST_USD,
        }.items():
            if hybrid_value <= 0:
                raise ValueError(f"{hybrid_name} must be positive")
        for name, value in {
            "ORION_SCRIPTING_MAX_PLAN_BYTES": self.ORION_SCRIPTING_MAX_PLAN_BYTES,
            "ORION_SCRIPTING_MAX_SCRIPT_BYTES": self.ORION_SCRIPTING_MAX_SCRIPT_BYTES,
            "ORION_SCRIPTING_MAX_RESPONSE_BYTES": self.ORION_SCRIPTING_MAX_RESPONSE_BYTES,
            "ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES": self.ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES,
            "ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES": self.ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES,
            "ORION_SCENE_PLANNING_MAX_PLAN_BYTES": self.ORION_SCENE_PLANNING_MAX_PLAN_BYTES,
            "ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES": self.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES,
            "ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES": self.ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES,
            "ORION_BINARY_ASSET_MAX_SIZE_BYTES": self.ORION_BINARY_ASSET_MAX_SIZE_BYTES,
            "ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES": self.ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES,
            "ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES": self.ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES,
            "ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES": self.ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES,
            "ORION_VIDEO_CLIP_GENERATION_MAX_SOURCE_MANIFEST_BYTES": self.ORION_VIDEO_CLIP_GENERATION_MAX_SOURCE_MANIFEST_BYTES,
            "ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES": self.ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES,
            "ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES": self.ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_RESPONSE_BYTES": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_RESPONSE_BYTES,
            "ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_VIDEO_BYTES": self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_VIDEO_BYTES,
            "ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES": self.ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES,
            "ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES": self.ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES,
            "ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES": self.ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES,
            "ORION_SPEECH_GENERATION_MAX_MANIFEST_BYTES": self.ORION_SPEECH_GENERATION_MAX_MANIFEST_BYTES,
            "ORION_SPEECH_GENERATION_MAX_SCRIPT_BYTES": self.ORION_SPEECH_GENERATION_MAX_SCRIPT_BYTES,
            "ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES": self.ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES,
            "ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES": self.ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES,
            "ORION_AUDIO_DESIGN_MAX_MANIFEST_BYTES": self.ORION_AUDIO_DESIGN_MAX_MANIFEST_BYTES,
            "ORION_AUDIO_DESIGN_MAX_SCRIPT_BYTES": self.ORION_AUDIO_DESIGN_MAX_SCRIPT_BYTES,
            "ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES,
            "ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES,
            "ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES,
            "ORION_RENDER_MAX_REQUEST_BYTES": self.ORION_RENDER_MAX_REQUEST_BYTES,
            "ORION_RENDER_MAX_MANIFEST_BYTES": self.ORION_RENDER_MAX_MANIFEST_BYTES,
            "ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES": self.ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES,
        }.items():
            maximum = {
                "ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES": 250_000_000,
                "ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES": 16_000_000,
            }.get(name, 50_000_000)
            if not 1 <= value <= maximum:
                raise ValueError(f"{name} is outside safe limits")
        for name, value in {
            "ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES,
            "ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES,
            "ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES": self.ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES,
            "ORION_RENDER_MAX_REQUEST_BYTES": self.ORION_RENDER_MAX_REQUEST_BYTES,
            "ORION_RENDER_MAX_MANIFEST_BYTES": self.ORION_RENDER_MAX_MANIFEST_BYTES,
            "ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES": self.ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES,
        }.items():
            if not 1_024 <= value <= 16_000_000:
                scope = (
                    "composition" if name.startswith("ORION_MEDIA_COMPOSITION_") else "rendering"
                )
                raise ValueError(f"{name} is outside safe {scope} limits")
        if not 0 <= self.ORION_MEDIA_COMPOSITION_MAXIMUM_ABSOLUTE_EXTENSION_MS <= 60_000:
            raise ValueError("media composition absolute duration extension is outside safe limits")
        if not (
            Decimal("0")
            <= self.ORION_MEDIA_COMPOSITION_MAXIMUM_RELATIVE_EXTENSION_RATIO
            <= Decimal("1")
        ):
            raise ValueError("media composition relative duration extension is outside safe limits")
        if not 1 <= self.ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES <= 100_000_000:
            raise ValueError("ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES is outside safe limits")
        if not 60 <= self.ORION_SPEECH_GENERATION_WORDS_PER_MINUTE <= 360:
            raise ValueError("speech words per minute is outside safe limits")
        if not 8_000 <= self.ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ <= 48_000:
            raise ValueError("speech sample rate is outside safe limits")
        if not 100 <= self.ORION_SPEECH_GENERATION_MIN_DURATION_MS <= 10_000:
            raise ValueError("minimum speech duration is outside safe limits")
        if not (
            self.ORION_SPEECH_GENERATION_MIN_DURATION_MS
            <= self.ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS
            <= 600_000
        ):
            raise ValueError("maximum speech duration is outside safe limits")
        speech_frames = (
            self.ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS
            * self.ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ
            + 999
        ) // 1_000
        speech_bytes = (
            44
            + speech_frames
            * self.ORION_SPEECH_GENERATION_CHANNEL_COUNT
            * self.ORION_SPEECH_GENERATION_SAMPLE_WIDTH_BYTES
        )
        if speech_bytes > self.ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES:
            raise ValueError("speech audio limit cannot hold maximum duration")
        if not (
            250
            <= self.ORION_AUDIO_DESIGN_MIN_MUSIC_DURATION_MS
            <= self.ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS
            <= 600_000
        ):
            raise ValueError("audio-design music duration limits are invalid")
        if not (
            20
            <= self.ORION_AUDIO_DESIGN_MIN_SOUND_EFFECT_DURATION_MS
            <= self.ORION_AUDIO_DESIGN_MAX_SOUND_EFFECT_DURATION_MS
            <= 30_000
        ):
            raise ValueError("audio-design SFX duration limits are invalid")
        audio_design_frames = (
            self.ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS * self.ORION_AUDIO_DESIGN_SAMPLE_RATE_HZ
            + 500
        ) // 1_000
        audio_design_bytes = (
            44
            + audio_design_frames
            * self.ORION_AUDIO_DESIGN_CHANNEL_COUNT
            * self.ORION_AUDIO_DESIGN_SAMPLE_WIDTH_BYTES
        )
        if audio_design_bytes > self.ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES:
            raise ValueError("audio-design audio limit cannot hold maximum music")
        if not 1 <= self.ORION_SPEECH_GENERATION_REMOTE_MAX_POLL_ATTEMPTS <= 1000:
            raise ValueError("remote speech poll attempts are outside safe limits")
        if not 1_024 <= self.ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES <= 4_000_000:
            raise ValueError("remote speech job size is outside safe limits")
        if not (0 < self.ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS <= 300):
            raise ValueError("remote speech poll interval is outside safe limits")
        remote_speech_enabled = self.ORION_SPEECH_GENERATION_REMOTE_PROVIDER == "openrouter"
        if self.ORION_SPEECH_GENERATION_PROVIDER == "openrouter" and not remote_speech_enabled:
            raise ValueError("OpenRouter speech requires remote_provider=openrouter")
        if remote_speech_enabled:
            if not self.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS:
                raise ValueError("OpenRouter speech requires explicit billable authorization")
            if not self.ORION_SPEECH_GENERATION_REMOTE_MODEL:
                raise ValueError("OpenRouter speech model is missing")
            if not self.ORION_SPEECH_GENERATION_REMOTE_VOICE:
                raise ValueError("OpenRouter speech voice is missing")
            speech_estimate = self.ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST
            speech_maximum = self.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST
            if (
                speech_estimate is None
                or speech_maximum is None
                or speech_estimate > speech_maximum
            ):
                raise ValueError("OpenRouter speech cost authorization is invalid")
            if speech_estimate * self.ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB > speech_maximum:
                raise ValueError("OpenRouter speech job cost authorization is invalid")
        elif (
            self.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS
            or self.ORION_SPEECH_GENERATION_REMOTE_MODEL is not None
            or self.ORION_SPEECH_GENERATION_REMOTE_VOICE is not None
            or self.ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST is not None
            or self.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST is not None
        ):
            raise ValueError(
                "disabled remote speech cannot configure billing, model, voice, or cost"
            )
        fitting_enabled = self.ORION_NARRATION_FITTING_PROVIDER == "openrouter"
        if not 0 <= self.ORION_NARRATION_FITTING_MAX_ATTEMPTS <= 5:
            raise ValueError("narration fitting attempts are outside safe limits")
        if not 0 <= self.ORION_NARRATION_FITTING_MAX_PROVIDER_RETRIES <= 1:
            raise ValueError("narration fitting provider retries are outside safe limits")
        if self.ORION_NARRATION_FITTING_MAX_TRANSPORT_ATTEMPTS != 1:
            raise ValueError("narration fitting does not permit automatic transport retries")
        if fitting_enabled:
            fitting_values = (
                self.ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT,
                self.ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT,
                self.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD,
            )
            if (
                not self.ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS
                or self.ORION_NARRATION_FITTING_MAX_ATTEMPTS < 1
                or not self.ORION_NARRATION_FITTING_MODEL.strip()
                or any(value is None for value in fitting_values)
            ):
                raise ValueError("OpenRouter narration fitting configuration is incomplete")
            estimate, per_attempt_maximum, job_maximum = fitting_values
            assert estimate is not None and per_attempt_maximum is not None
            assert job_maximum is not None
            if estimate <= 0 or estimate > per_attempt_maximum or estimate > job_maximum:
                raise ValueError("OpenRouter narration fitting cost authorization is invalid")
        elif (
            self.ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS
            or self.ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT is not None
            or self.ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT is not None
            or self.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD is not None
        ):
            raise ValueError("disabled narration fitting cannot authorize billing")
        if (
            self.ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS
            > self.ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS
        ):
            raise ValueError("video clip duration cannot exceed the configured maximum")
        if self.ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES > 250_000_000:
            raise ValueError("ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES is outside safe limits")
        if not 30 <= self.ORION_ASSET_PUBLISHING_LIFETIME_SECONDS <= 86_400:
            raise ValueError("asset publication lifetime is outside safe limits")
        if not 1 <= self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_ATTEMPTS <= 1000:
            raise ValueError("OpenRouter video poll attempts are outside safe limits")
        if self.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD <= 0:
            raise ValueError("OpenRouter video maximum cost must be positive")
        if self.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD <= 0:
            raise ValueError("OpenRouter video maximum job cost must be positive")
        if not 1 <= self.ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB <= 50:
            raise ValueError("OpenRouter video request limit is outside safe bounds")
        parsed_video_url = urlsplit(self.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_BASE_URL)
        if (
            parsed_video_url.scheme != "https"
            or parsed_video_url.hostname != "openrouter.ai"
            or parsed_video_url.path.rstrip("/") != "/api/v1"
            or parsed_video_url.username is not None
            or parsed_video_url.password is not None
            or parsed_video_url.query
            or parsed_video_url.fragment
        ):
            raise ValueError("OpenRouter video base URL must be the official API")
        if (
            self.ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS
            and self.ORION_VIDEO_CLIP_GENERATION_PROVIDER != "openrouter"
        ):
            raise ValueError("billable video authorization requires provider=openrouter")
        if self.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "openrouter" and (
            not self.ORION_VIDEO_CLIP_GENERATION_MODEL.strip()
            or self.ORION_VIDEO_CLIP_GENERATION_MODEL == "simulated-video-v1"
        ):
            raise ValueError("OpenRouter video requires an explicit model ID")
        if self.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER == "filesystem" and (
            self.ORION_ASSET_PUBLISHING_PUBLISHER != "filesystem"
        ):
            raise ValueError("filesystem video frames require filesystem asset publishing")
        if self.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "openrouter" and (
            self.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER != "filesystem"
        ):
            raise ValueError("OpenRouter video requires filesystem first-frame publishing")
        for name in (
            "ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH",
            "ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH",
        ):
            value = getattr(self, name)
            if value is not None:
                normalized = value.strip()
                if not normalized or any(ord(character) < 32 for character in normalized):
                    raise ValueError(f"{name} is invalid")
                setattr(self, name, normalized)
        if self.ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS != 1:
            raise ValueError("OpenRouter image transport attempts must equal one")
        if not 1 <= self.ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB <= 50:
            raise ValueError("image request limit is outside safe bounds")
        if not 1 <= self.ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB <= 50:
            raise ValueError("speech request limit is outside safe bounds")
        if not 1_024 <= self.ORION_IMAGE_ACQUISITION_MAX_REQUEST_RECORD_BYTES <= 4_000_000:
            raise ValueError("image request record limit is outside safe bounds")
        if (
            self.ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES
            > self.ORION_BINARY_ASSET_MAX_SIZE_BYTES
        ):
            raise ValueError("decoded image limit cannot exceed binary asset storage limit")
        if self.ORION_IMAGE_ACQUISITION_PROVIDER_ONLY is not None:
            provider_only = self.ORION_IMAGE_ACQUISITION_PROVIDER_ONLY.strip().lower()
            if (
                not provider_only
                or len(provider_only) > 100
                or not provider_only[0].isalnum()
                or any(
                    not (character.isalnum() or character in {"_", "-"})
                    for character in provider_only
                )
            ):
                raise ValueError("ORION_IMAGE_ACQUISITION_PROVIDER_ONLY is invalid")
            self.ORION_IMAGE_ACQUISITION_PROVIDER_ONLY = provider_only
        if self.ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS < 0:
            raise ValueError("ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS cannot be negative")
        quarantine = self.ORION_PLANNING_QUARANTINE_DIR.strip()
        posix_path = PurePosixPath(quarantine)
        windows_path = PureWindowsPath(quarantine)
        if (
            not quarantine
            or "\\" in quarantine
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or ".." in posix_path.parts
        ):
            raise ValueError("ORION_PLANNING_QUARANTINE_DIR must be a safe relative POSIX path")
        self.ORION_PLANNING_QUARANTINE_DIR = posix_path.as_posix()
        if self.ORION_OPENROUTER_HTTP_REFERER is not None:
            referer = self.ORION_OPENROUTER_HTTP_REFERER.strip()
            parsed_referer = urlsplit(referer)
            if (
                not referer
                or len(referer) > 2048
                or any(ord(char) < 32 for char in referer)
                or parsed_referer.scheme not in {"http", "https"}
                or not parsed_referer.hostname
                or parsed_referer.username
                or parsed_referer.password
            ):
                raise ValueError("ORION_OPENROUTER_HTTP_REFERER is invalid")
            self.ORION_OPENROUTER_HTTP_REFERER = referer
        if self.ORION_OPENROUTER_APP_TITLE is not None:
            title = self.ORION_OPENROUTER_APP_TITLE.strip()
            if not title or len(title) > 200 or any(ord(char) < 32 for char in title):
                raise ValueError("ORION_OPENROUTER_APP_TITLE is invalid")
            self.ORION_OPENROUTER_APP_TITLE = title
        return self

    @property
    def production_database_url(self) -> str:
        """Return an explicit URL or the safe default under ORION_HOME."""

        if self.ORION_DATABASE_URL:
            return self.ORION_DATABASE_URL
        from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path

        return sqlite_url_from_path(self.ORION_HOME / "orion.db")


settings = Settings()
