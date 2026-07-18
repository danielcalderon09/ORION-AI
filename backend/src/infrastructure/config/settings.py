"""Application settings and configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Orion AI application settings."""

    # App
    APP_NAME: str = "Orion AI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ORION_PROMPT_VIDEO_ENABLED: bool = False

    # Database
    ORION_DATABASE_URL: str | None = None
    ORION_DATABASE_ECHO: bool = False

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure directories exist
        self.ORION_HOME.mkdir(parents=True, exist_ok=True)
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def production_database_url(self) -> str:
        """Return an explicit URL or the safe default under ORION_HOME."""

        if self.ORION_DATABASE_URL:
            return self.ORION_DATABASE_URL
        from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path

        return sqlite_url_from_path(self.ORION_HOME / "orion.db")


settings = Settings()
