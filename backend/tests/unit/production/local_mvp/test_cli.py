from decimal import Decimal

import pytest

from backend.src.production.cli import generate_video


def test_cli_rejects_arbitrary_mode() -> None:
    with pytest.raises(SystemExit):
        generate_video._parser().parse_args(  # noqa: SLF001 - CLI contract test
            ["--prompt", "Marte", "--mode", "dry_run"]
        )


def test_cli_returns_safe_error_without_traceback(monkeypatch, capsys) -> None:
    async def fail(_args):
        raise RuntimeError("FFmpeg is missing")

    monkeypatch.setattr(generate_video, "_run", fail)
    assert generate_video.main(["--prompt", "Marte"]) == 2
    captured = capsys.readouterr()
    assert "FFmpeg is missing" in captured.err
    assert "Traceback" not in captured.err


def test_cli_returns_application_exit_code(monkeypatch) -> None:
    async def succeed(_args):
        return 0

    monkeypatch.setattr(generate_video, "_run", succeed)
    assert generate_video.main(["--prompt", "Marte", "--output-summary"]) == 0


def test_cli_accepts_one_scene_for_controlled_provider_test() -> None:
    args = generate_video._parser().parse_args(  # noqa: SLF001 - CLI contract test
        ["--prompt", "Marte", "--scene-count", "1"]
    )
    assert args.scene_count == 1


def test_cli_accepts_explicit_failed_job_retry() -> None:
    args = generate_video._parser().parse_args(  # noqa: SLF001 - CLI contract test
        [
            "--resume-job-id",
            "1abbd29b-66c0-4544-ba32-b2cf9afd4dba",
            "--retry-failed",
        ]
    )
    assert args.retry_failed


def test_local_mvp_settings_forwards_only_explicit_scripting_environment(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("ORION_SCRIPTING_PROVIDER", "openrouter")
    monkeypatch.setenv("ORION_SCRIPTING_MODEL", "vendor/explicit")
    monkeypatch.setenv("ORION_SCRIPTING_API_KEY", "runtime-secret")
    monkeypatch.setenv("ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS", "true")
    monkeypatch.setenv("ORION_SCRIPTING_ESTIMATED_COST_USD", "0.01")
    monkeypatch.setenv("ORION_SCRIPTING_MAX_ESTIMATED_COST_USD", "0.10")
    configured = generate_video.local_mvp_settings()
    assert configured.ORION_SCRIPTING_PROVIDER == "openrouter"
    assert configured.ORION_SCRIPTING_MODEL == "vendor/explicit"
    assert configured.ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS
    assert Decimal("0.01") == configured.ORION_SCRIPTING_ESTIMATED_COST_USD
    assert "runtime-secret" not in repr(configured)
    assert configured.ORION_PLANNING_PROVIDER == "simulated"


def test_local_mvp_settings_forwards_explicit_openrouter_video_environment(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_PROVIDER", "openrouter")
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_MODEL", "google/veo-3.1-lite")
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS", "true")
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD", "0.20")
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB", "1")
    monkeypatch.setenv("ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER", "filesystem")
    monkeypatch.setenv("ORION_ASSET_PUBLISHING_PUBLISHER", "filesystem")
    monkeypatch.setenv("ORION_ASSET_PUBLISHING_PUBLIC_ROOT", str(tmp_path / "public"))
    monkeypatch.setenv(
        "ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL", "https://media.example.test/orion"
    )
    configured = generate_video.local_mvp_settings()
    assert configured.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "openrouter"
    assert configured.ORION_VIDEO_CLIP_GENERATION_MODEL == "google/veo-3.1-lite"
    assert Decimal("0.20") == configured.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD
    assert configured.ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB == 1
    assert configured.ORION_ASSET_PUBLISHING_PUBLISHER == "filesystem"


def test_local_mvp_settings_forwards_narration_fitting_authorization(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("ORION_NARRATION_FITTING_PROVIDER", "openrouter")
    monkeypatch.setenv("ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS", "true")
    monkeypatch.setenv(
        "ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT", "0.001"
    )
    monkeypatch.setenv(
        "ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT", "0.002"
    )
    monkeypatch.setenv("ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD", "0.008")
    monkeypatch.setenv("ORION_NARRATION_FITTING_MAX_PROVIDER_RETRIES", "0")

    configured = generate_video.local_mvp_settings()

    assert configured.ORION_NARRATION_FITTING_PROVIDER == "openrouter"
    assert configured.ORION_NARRATION_FITTING_MAX_ATTEMPTS == 2
    assert Decimal("0.008") == (
        configured.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD
    )
    assert configured.ORION_NARRATION_FITTING_MAX_PROVIDER_RETRIES == 0
