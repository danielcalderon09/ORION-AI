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


def test_local_mvp_settings_forwards_only_explicit_scripting_environment(
    monkeypatch, tmp_path
) -> None:
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
