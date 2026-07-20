"""Dual JSON artifact reconciliation coverage."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.tests.unit.production.scripting.conftest import JOB_ID

NOW = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)


class RegisteredReader:
    def __init__(self, paths=()) -> None:
        self.paths = frozenset(paths)

    def list_registered_paths(self):
        return self.paths


def create(root, stage, filename):
    relative = f"production/{JOB_ID}/{stage}/attempt-1/{filename}"
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    timestamp = (NOW - timedelta(seconds=600)).timestamp()
    os.utime(target, (timestamp, timestamp))
    return relative, target


@pytest.mark.asyncio
async def test_reconciler_handles_plan_and_script_but_no_other_stage(tmp_path) -> None:
    plan_relative, plan = create(tmp_path, "planning", "production-plan.json")
    _, script = create(tmp_path, "scripting", "production-script.json")
    _, unrelated = create(tmp_path, "scene_planning", "production-script.json")
    reconciler = LocalProductionArtifactReconciler(
        workspace_root=tmp_path,
        registered_reader=RegisteredReader((plan_relative,)),
        minimum_age_seconds=300,
        action="quarantine",
        clock=lambda: NOW,
    )
    report = await reconciler.reconcile()
    assert plan.is_file()
    assert not script.exists()
    assert unrelated.is_file()
    assert report.registered == 1
    assert report.quarantined == 1
