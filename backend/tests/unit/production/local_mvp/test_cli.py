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
