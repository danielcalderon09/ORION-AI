"""Tests for the prompt-to-video feature switch."""

import pytest


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
    assert settings.ORION_PLANNING_RECONCILE_ARTIFACTS is True
    assert settings.ORION_PLANNING_ORPHAN_ACTION == "quarantine"
    assert settings.ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS == 300


def test_planning_quarantine_settings_reject_unsafe_paths(tmp_path) -> None:
    from pydantic import ValidationError

    from backend.src.infrastructure.config.settings import Settings

    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
    }
    for unsafe in ("../quarantine", "/absolute", "C:\\quarantine"):
        with pytest.raises(ValidationError, match="safe relative POSIX path"):
            Settings(**values, ORION_PLANNING_QUARANTINE_DIR=unsafe)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_OPENROUTER_HTTP_REFERER", "ftp://orion.example"),
        ("ORION_OPENROUTER_HTTP_REFERER", "https://user:pass@orion.example"),
        ("ORION_OPENROUTER_HTTP_REFERER", "https:///missing-host"),
        ("ORION_OPENROUTER_APP_TITLE", "bad\nheader"),
        ("ORION_OPENROUTER_APP_TITLE", "x" * 201),
    ],
)
def test_openrouter_optional_headers_are_validated(tmp_path, name, value) -> None:
    from pydantic import ValidationError

    from backend.src.infrastructure.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            ORION_HOME=tmp_path / "home",
            MODELS_DIR=tmp_path / "models",
            PROJECTS_DIR=tmp_path / "projects",
            TEMP_DIR=tmp_path / "temp",
            **{name: value},
        )


def test_production_defaults_are_offline_and_non_billable(tmp_path) -> None:
    from backend.src.infrastructure.config.settings import Settings

    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
    )

    assert settings.ORION_PLANNING_PROVIDER == "simulated"
    assert settings.ORION_SCRIPTING_PROVIDER == "simulated"
    assert settings.ORION_SCENE_PLANNING_PROVIDER == "simulated"
    assert settings.ORION_VISUAL_ASSET_PLANNING_PROVIDER == "simulated"
    assert settings.ORION_IMAGE_ACQUISITION_PROVIDER == "simulated"
    assert settings.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS is False
    assert settings.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER == "disabled"
    assert settings.ORION_ASSET_PUBLISHING_PUBLISHER == "null"
    assert settings.ORION_SPEECH_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_SPEECH_GENERATION_VOICE == "simulated-neutral-v1"
    assert settings.ORION_SPEECH_GENERATION_LANGUAGE == "es-ES"
    assert settings.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS is False
    assert settings.ORION_SPEECH_GENERATION_REMOTE_PROVIDER == "disabled"
    assert settings.ORION_SPEECH_GENERATION_REMOTE_MODEL is None
    assert settings.ORION_SPEECH_GENERATION_REMOTE_VOICE is None
    assert settings.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST is None
    assert settings.ORION_PLANNING_API_KEY is None
    assert settings.ORION_SCRIPTING_API_KEY is None
    assert settings.ORION_SCENE_PLANNING_API_KEY is None
    assert settings.ORION_VISUAL_ASSET_PLANNING_API_KEY is None
    assert settings.ORION_IMAGE_ACQUISITION_API_KEY is None
    assert settings.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY is None


def test_visual_strategy_accepts_image_only_without_changing_default(tmp_path) -> None:
    from backend.src.infrastructure.config.settings import Settings

    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
    }

    assert Settings(**values).ORION_VISUAL_STRATEGY == "full_video"
    assert (
        Settings(**values, ORION_VISUAL_STRATEGY="image_only").ORION_VISUAL_STRATEGY
        == "image_only"
    )
    image_only = Settings(
        **values,
        ORION_VISUAL_STRATEGY="image_only",
        ORION_VIDEO_CLIP_GENERATION_PROVIDER="openrouter",
        ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS=False,
    )
    assert image_only.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "openrouter"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_SPEECH_GENERATION_WORDS_PER_MINUTE", 59),
        ("ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ", 96_000),
        ("ORION_SPEECH_GENERATION_MIN_DURATION_MS", 99),
        ("ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS", 700_000),
        ("ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES", 1_023),
    ],
)
def test_speech_settings_reject_unsafe_limits(tmp_path, name, value) -> None:
    from pydantic import ValidationError

    from backend.src.infrastructure.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            ORION_HOME=tmp_path / "home",
            MODELS_DIR=tmp_path / "models",
            PROJECTS_DIR=tmp_path / "projects",
            TEMP_DIR=tmp_path / "temp",
            **{name: value},
        )
