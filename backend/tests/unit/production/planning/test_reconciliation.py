"""Planning artifact orphan reconciliation and path safety tests."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalPlanningArtifactReconciler,
)
from backend.src.production.planning.reconciliation import (
    PlanningArtifactReconciliationError,
)

NOW = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000801")


class RegisteredReader:
    def __init__(self, paths: set[str] | None = None) -> None:
        self.paths = frozenset(paths or set())

    def list_registered_paths(self) -> frozenset[str]:
        return self.paths


def create_plan(root: Path, *, attempt: int = 1, age_seconds: float = 600) -> tuple[str, Path]:
    relative = f"production/{JOB_ID}/planning/attempt-{attempt}/production-plan.json"
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    timestamp = (NOW - timedelta(seconds=age_seconds)).timestamp()
    os.utime(target, (timestamp, timestamp))
    return relative, target


def reconciler(
    root: Path,
    *,
    registered: set[str] | None = None,
    age: float = 300,
    action: str = "quarantine",
) -> LocalPlanningArtifactReconciler:
    return LocalPlanningArtifactReconciler(
        workspace_root=root,
        registered_reader=RegisteredReader(registered),
        minimum_age_seconds=age,
        action=action,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_registered_artifact_is_never_removed(tmp_path) -> None:
    relative, target = create_plan(tmp_path)
    report = await reconciler(tmp_path, registered={relative}).reconcile()
    assert target.is_file()
    assert report.registered == 1
    assert report.orphaned == 0


@pytest.mark.asyncio
async def test_old_orphan_is_quarantined_idempotently(tmp_path) -> None:
    _, target = create_plan(tmp_path)
    first = await reconciler(tmp_path).reconcile()
    quarantined = (
        tmp_path
        / "production-quarantine"
        / str(JOB_ID)
        / "planning"
        / "attempt-1"
        / "production-plan.json"
    )
    assert not target.exists()
    assert first.scanned == 1
    assert first.orphaned == 1
    assert first.quarantined == 1
    assert quarantined.is_file()
    second = await reconciler(tmp_path).reconcile()
    assert second.scanned == 0
    assert second.quarantined == 0


@pytest.mark.asyncio
async def test_recent_orphan_is_skipped(tmp_path) -> None:
    _, target = create_plan(tmp_path, age_seconds=10)
    report = await reconciler(tmp_path).reconcile()
    assert target.exists()
    assert report.orphaned == 1
    assert report.skipped_recent == 1


@pytest.mark.asyncio
async def test_delete_action_only_touches_planning_contract_path(tmp_path) -> None:
    _, target = create_plan(tmp_path)
    other = tmp_path / "production" / str(JOB_ID) / "rendering" / "production-plan.json"
    other.parent.mkdir(parents=True)
    other.write_text("do not touch", encoding="utf-8")
    report = await reconciler(tmp_path, action="delete").reconcile()
    assert not target.exists()
    assert other.read_text(encoding="utf-8") == "do not touch"
    assert report.deleted == 1


@pytest.mark.asyncio
async def test_symlinked_directory_outside_workspace_fails_closed(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_file = outside / "production-plan.json"
    outside_file.write_text("outside", encoding="utf-8")
    production = tmp_path / "production"
    production.mkdir()
    link = production / str(JOB_ID)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(PlanningArtifactReconciliationError) as captured:
        await reconciler(tmp_path, action="delete").reconcile()
    assert captured.value.report.errors == 1
    assert outside_file.read_text(encoding="utf-8") == "outside"


@pytest.mark.asyncio
async def test_symlinked_target_is_not_deleted(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-target"
    outside.write_text("outside", encoding="utf-8")
    _, target = create_plan(tmp_path)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(PlanningArtifactReconciliationError) as captured:
        await reconciler(tmp_path, action="delete").reconcile()
    assert captured.value.report.scanned == 1
    assert captured.value.report.errors == 1
    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.asyncio
async def test_cleanup_error_is_reported_with_original_cause(monkeypatch, tmp_path) -> None:
    create_plan(tmp_path)
    original_rmdir = Path.rmdir

    def fail_attempt_cleanup(path: Path) -> None:
        if path.name == "attempt-1":
            raise PermissionError("simulated cleanup denial")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", fail_attempt_cleanup)
    with pytest.raises(PlanningArtifactReconciliationError) as captured:
        await reconciler(tmp_path).reconcile()
    assert captured.value.report.quarantined == 1
    assert captured.value.report.errors == 1
    assert isinstance(captured.value.__cause__, PermissionError)
