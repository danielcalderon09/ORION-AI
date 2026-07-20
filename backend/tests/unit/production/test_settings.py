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
