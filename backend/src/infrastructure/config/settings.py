"""Application settings and configuration."""

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
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
    ORION_SCRIPTING_PROVIDER: str = "simulated"
    ORION_SCRIPTING_MODEL: str = "openai/gpt-4.1-mini"
    ORION_SCRIPTING_API_KEY: SecretStr | None = None
    ORION_SCRIPTING_BASE_URL: str = "https://openrouter.ai/api/v1"
    ORION_SCRIPTING_TIMEOUT_SECONDS: float = 30.0
    ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS: int = 2
    ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS: float = 0.25
    ORION_SCRIPTING_MAX_OUTPUT_TOKENS: int = 8192
    ORION_SCRIPTING_TEMPERATURE: float = 0.2
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
        if not 1 <= self.ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError("ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS must be between 1 and 5")
        if not 1 <= self.ORION_SCRIPTING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_SCRIPTING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_SCRIPTING_TEMPERATURE <= 2:
            raise ValueError("ORION_SCRIPTING_TEMPERATURE must be between 0 and 2")
        if not 1 <= self.ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError(
                "ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS must be between 1 and 5"
            )
        if not 1 <= self.ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError("ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS is outside safe limits")
        if not 0 <= self.ORION_SCENE_PLANNING_TEMPERATURE <= 2:
            raise ValueError("ORION_SCENE_PLANNING_TEMPERATURE must be between 0 and 2")
        if not 1 <= self.ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS <= 5:
            raise ValueError(
                "ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS "
                "must be between 1 and 5"
            )
        if not 1 <= self.ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS <= 100_000:
            raise ValueError(
                "ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS is outside safe limits"
            )
        if not 0 <= self.ORION_VISUAL_ASSET_PLANNING_TEMPERATURE <= 2:
            raise ValueError(
                "ORION_VISUAL_ASSET_PLANNING_TEMPERATURE must be between 0 and 2"
            )
        for name, value in {
            "ORION_SCRIPTING_MAX_PLAN_BYTES": self.ORION_SCRIPTING_MAX_PLAN_BYTES,
            "ORION_SCRIPTING_MAX_SCRIPT_BYTES": self.ORION_SCRIPTING_MAX_SCRIPT_BYTES,
            "ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES": self.ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES,
            "ORION_SCENE_PLANNING_MAX_PLAN_BYTES": self.ORION_SCENE_PLANNING_MAX_PLAN_BYTES,
            "ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES": self.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES,
            "ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES": self.ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES,
        }.items():
            if not 1 <= value <= 50_000_000:
                raise ValueError(f"{name} is outside safe limits")
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
            if (
                not title
                or len(title) > 200
                or any(ord(char) < 32 for char in title)
            ):
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
