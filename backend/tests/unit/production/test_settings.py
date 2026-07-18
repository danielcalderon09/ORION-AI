"""Tests for the prompt-to-video feature switch."""


def test_prompt_video_feature_flag_defaults_to_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ORION_PROMPT_VIDEO_ENABLED", raising=False)

    from backend.src.infrastructure.config.settings import Settings

    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
    )

    assert settings.ORION_PROMPT_VIDEO_ENABLED is False
