"""Binary reconciliation and dependency injection contracts."""

import pytest

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.binary_assets.exceptions import BinaryAssetLinkError
from backend.src.production.binary_assets.models import (
    ProductionBinaryAssetReference,
)
from backend.src.production.binary_assets.reconciliation import (
    BinaryAssetReconciliationIssueKind,
    FilesystemBinaryAssetReconciler,
)
from backend.src.production.composition.container import build_production_container
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)
from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.tests.unit.production.binary_assets.test_filesystem_store import (
    request,
)


@pytest.mark.asyncio
async def test_reconciliation_accepts_valid_pair(
    binary_store,
    storage_configuration,
    png_bytes,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    reconciler = FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    )
    report = await reconciler.reconcile()
    assert (report.scanned, report.valid, report.issues) == (1, 1, ())
    read = await binary_store.read(
        reference=ProductionBinaryAssetReference.from_asset(asset)
    )
    assert read.content == png_bytes


@pytest.mark.asyncio
async def test_reconciliation_reports_file_without_metadata(
    binary_store,
    storage_configuration,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.with_name(f"{target.name}.asset.json").unlink()
    report = await FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    ).reconcile()
    assert report.issues[0].kind == (
        BinaryAssetReconciliationIssueKind.FILE_WITHOUT_METADATA
    )


@pytest.mark.asyncio
async def test_reconciliation_reports_metadata_without_file(
    binary_store,
    storage_configuration,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    tmp_path.joinpath(*asset.storage_path.split("/")).unlink()
    report = await FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    ).reconcile()
    assert report.issues[0].kind == (
        BinaryAssetReconciliationIssueKind.METADATA_WITHOUT_FILE
    )


@pytest.mark.asyncio
async def test_reconciliation_reports_corruption(
    binary_store,
    storage_configuration,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    tmp_path.joinpath(*asset.storage_path.split("/")).write_bytes(b"corrupt")
    report = await FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    ).reconcile()
    assert report.issues[0].kind in {
        BinaryAssetReconciliationIssueKind.HASH_MISMATCH,
        BinaryAssetReconciliationIssueKind.SIZE_MISMATCH,
        BinaryAssetReconciliationIssueKind.CORRUPT_FILE,
    }


@pytest.mark.asyncio
async def test_reconciliation_reports_unsafe_link(
    binary_store,
    storage_configuration,
    png_bytes,
    monkeypatch,
) -> None:
    await binary_store.write(request=request(), content=png_bytes)
    reconciler = FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    )

    def reject_link(path) -> None:
        raise BinaryAssetLinkError("test link")

    monkeypatch.setattr(
        reconciler._confinement,
        "reject_unsafe_file",
        reject_link,
    )
    report = await reconciler.reconcile()
    assert report.issues[0].kind == BinaryAssetReconciliationIssueKind.UNSAFE_LINK


@pytest.mark.asyncio
async def test_common_artifact_reconciler_includes_binary_counts(
    binary_store,
    storage_configuration,
    png_bytes,
) -> None:
    await binary_store.write(request=request(), content=png_bytes)
    binary_reconciler = FilesystemBinaryAssetReconciler(
        configuration=storage_configuration,
        store=binary_store,
    )

    class EmptyRegisteredReader:
        def list_registered_paths(self):
            return frozenset()

    report = await LocalProductionArtifactReconciler(
        workspace_root=storage_configuration.workspace,
        registered_reader=EmptyRegisteredReader(),
        binary_reconciler=binary_reconciler,
    ).reconcile()
    assert (report.binary_scanned, report.binary_valid, report.binary_issues) == (
        1,
        1,
        0,
    )


def test_container_registers_one_store_for_both_ports(tmp_path) -> None:
    configured = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "production.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
    )
    container = build_production_container(configured)
    try:
        assert container.binary_asset_store is container.binary_asset_writer
        assert container.binary_asset_store is container.binary_asset_reader
        assert container.binary_asset_configuration.workspace == configured.PROJECTS_DIR
        assert container.binary_asset_reconciler is not None
    finally:
        container.shutdown()


def test_storage_settings_defaults_and_limits(tmp_path) -> None:
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
    }
    configured = Settings(**values)
    assert configured.ORION_BINARY_ASSET_MAX_SIZE_BYTES == 25_000_000
    with pytest.raises(ValueError):
        Settings(**values, ORION_BINARY_ASSET_MAX_SIZE_BYTES=0)


def test_binary_assets_do_not_depend_on_providers_or_http() -> None:
    import backend.src.production.binary_assets.filesystem_store as module

    source_names = set(module.__dict__)
    assert "httpx" not in source_names
    assert not any("Provider" in name for name in source_names)
