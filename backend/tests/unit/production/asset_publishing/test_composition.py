"""Composition remains disabled, lazy, and outside the historical pipeline."""

from pathlib import Path

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "ORION_DATABASE_URL": sqlite_url_from_path(tmp_path / "composition.db"),
        "ORION_PROMPT_VIDEO_ENABLED": True,
        "ORION_PRODUCTION_WORKER_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_container_defaults_to_null_publisher(tmp_path: Path) -> None:
    container = build_production_container(_settings(tmp_path))
    assert container.asset_publisher.name == "null"
    assert container.async_resources[-1] is container.asset_publisher
    assert container.asset_publishing_service is not None
    assert container.asset_publishing_cleanup is not None
    assert container.asset_publishing_reconciler is not None
    container.shutdown()


def test_filesystem_publisher_is_lazy_at_startup(tmp_path: Path) -> None:
    public_root = tmp_path / "public"
    container = build_production_container(
        _settings(
            tmp_path,
            ORION_ASSET_PUBLISHING_PUBLISHER="filesystem",
            ORION_ASSET_PUBLISHING_PUBLIC_ROOT=public_root,
        )
    )
    assert container.asset_publisher.name == "filesystem"
    assert not public_root.exists()
    container.shutdown()


async def test_publisher_closes_once_with_container(tmp_path: Path) -> None:
    container = build_production_container(_settings(tmp_path))
    await container.aclose()


def test_no_asset_publishing_stage_was_added() -> None:
    assert "asset_publishing" not in {stage.value for stage in ProductionStage}
    assert "publishing_assets" not in {stage.value for stage in ProductionStage}
