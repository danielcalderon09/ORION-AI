"""Handler recovery matrix and read-only reconciliation."""

from pathlib import Path

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import RenderingSourceError
from backend.src.production.rendering.handler import LocalRenderPreparationHandler
from backend.src.production.rendering.manifest_store import (
    LocalRenderPreparationStore,
)
from backend.src.production.rendering.models import RenderManifestStatus
from backend.src.production.rendering.reconciliation import LocalRenderReconciler
from backend.src.production.rendering.recovery import (
    prepare_manifest,
    validating_manifest,
)
from backend.src.production.rendering.renderers import DryRunRenderer
from backend.src.production.rendering.request_builder import (
    build_local_render_request,
)
from backend.tests.unit.production.rendering.conftest import (
    NOW,
    StaticVerifiedSourceReader,
    make_render_command_context,
    make_verified_source,
)


class CountingDryRunRenderer(DryRunRenderer):
    def __init__(self) -> None:
        self.calls = 0

    async def prepare_or_validate(self, request):
        self.calls += 1
        return await super().prepare_or_validate(request)


class UnexpectedOutputStore(LocalRenderPreparationStore):
    async def output_exists(self, *, relative_path: str) -> bool:
        del relative_path
        return True


class MissingSourceReader:
    async def read(self, *, context):
        del context
        raise RenderingSourceError("source_missing", "source is absent")


def _store(tmp_path: Path) -> LocalRenderPreparationStore:
    return LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )


def _handler(
    tmp_path: Path,
    renderer: DryRunRenderer,
    *,
    source=None,
) -> LocalRenderPreparationHandler:
    return LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(source or make_verified_source()),
        store=_store(tmp_path),
        renderer=renderer,
        configuration=RenderingConfiguration(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_handler_validates_idempotently_and_emits_no_video(
    tmp_path: Path,
) -> None:
    command, context = make_render_command_context()
    renderer = CountingDryRunRenderer()
    handler = _handler(tmp_path, renderer)
    first = await handler.execute(command, context)
    second = await handler.execute(command, context)
    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert first.artifacts == second.artifacts
    assert renderer.calls == 1
    assert {item.artifact_type for item in first.artifacts} == {
        ArtifactType.LOCAL_RENDER_REQUEST,
        ArtifactType.RENDER_EXECUTION_MANIFEST,
    }
    assert ArtifactType.LONG_FORM_RENDER not in {item.artifact_type for item in first.artifacts}
    assert all(item.mime_type == "application/json" for item in first.artifacts)
    assert first.result.metadata["media_produced"] is False
    assert first.result.metadata["real_renderer_executed"] is False
    assert first.result.metadata["preparation_validated"] is True
    assert not tuple(tmp_path.rglob("*.mp4"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint",
    ["request_only", "manifest_only", "prepared", "validating"],
)
async def test_handler_recovers_partial_durable_state(
    tmp_path: Path,
    checkpoint: str,
) -> None:
    command, context = make_render_command_context()
    store = _store(tmp_path)
    renderer = CountingDryRunRenderer()
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    if checkpoint != "manifest_only":
        await store.write_request(context=context, request=request)
    if checkpoint != "request_only":
        manifest = prepare_manifest(
            request=request,
            attempt_number=context.attempt_number,
            capabilities=renderer.capabilities,
            now=NOW,
        )
        await store.create_manifest(context=context, manifest=manifest)
        if checkpoint == "validating":
            await store.checkpoint_manifest(
                context=context,
                previous=manifest,
                current=validating_manifest(manifest, now=NOW),
            )
    handler = LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=store,
        renderer=renderer,
        configuration=RenderingConfiguration(),
        clock=lambda: NOW,
    )
    output = await handler.execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert (await store.read_manifest(context=context)).status is RenderManifestStatus.VALIDATED
    assert renderer.calls == 1


@pytest.mark.asyncio
async def test_handler_rejects_stale_corrupt_and_unexpected_output(
    tmp_path: Path,
) -> None:
    command, context = make_render_command_context()
    store = _store(tmp_path)
    old_request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    await store.write_request(context=context, request=old_request)
    changed_source = make_verified_source(MediaCompositionConfiguration(fade_duration_ms=300))
    stale = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(changed_source),
        store=store,
        renderer=DryRunRenderer(),
        configuration=RenderingConfiguration(),
        clock=lambda: NOW,
    ).execute(command, context)
    assert stale.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert stale.result.error_code == "render_preparation_stale_source"

    other_root = tmp_path / "unexpected"
    conflict_store = UnexpectedOutputStore(
        other_root,
        max_request_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    conflict = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=conflict_store,
        renderer=DryRunRenderer(),
        configuration=RenderingConfiguration(),
        clock=lambda: NOW,
    ).execute(command, context)
    assert conflict.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert conflict.result.error_code == "render_output_conflict"
    assert not tuple(other_root.rglob("*.mp4"))


@pytest.mark.asyncio
async def test_handler_rejects_corrupt_request_and_manifest(tmp_path: Path) -> None:
    command, context = make_render_command_context()
    request_target = tmp_path.joinpath(
        "production",
        str(context.job_id),
        "rendering",
        f"attempt-{context.attempt_number}",
        "local-render-request.json",
    )
    request_target.parent.mkdir(parents=True)
    request_target.write_bytes(b"{corrupt")
    request_failure = await _handler(tmp_path, DryRunRenderer()).execute(
        command,
        context,
    )
    assert request_failure.result.outcome is StageOutcome.FAILED_PERMANENT
    assert request_failure.result.error_code == "render_preparation_invalid"

    manifest_root = tmp_path / "manifest"
    store = _store(manifest_root)
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    await store.write_request(context=context, request=request)
    manifest_target = manifest_root.joinpath(
        "production",
        str(context.job_id),
        "rendering",
        f"attempt-{context.attempt_number}",
        "render-execution-manifest.json",
    )
    manifest_target.write_bytes(b"{corrupt")
    manifest_failure = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=store,
        renderer=DryRunRenderer(),
        configuration=RenderingConfiguration(),
        clock=lambda: NOW,
    ).execute(command, context)
    assert manifest_failure.result.outcome is StageOutcome.FAILED_PERMANENT
    assert manifest_failure.result.error_code == "render_preparation_invalid"


@pytest.mark.asyncio
async def test_reconciler_is_read_only_and_reports_complete_state(
    tmp_path: Path,
) -> None:
    command, context = make_render_command_context()
    del command
    store = _store(tmp_path)
    renderer = CountingDryRunRenderer()
    await _handler(tmp_path, renderer).execute(*make_render_command_context())
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }
    result = await LocalRenderReconciler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=store,
        configuration=RenderingConfiguration(),
    ).reconcile(context=context)
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }
    assert before == after
    assert renderer.calls == 1
    assert result.stage_complete is True
    assert result.dry_run_accepted is True
    assert result.media_produced is False
    assert result.unexpected_output_file is False
    assert result.manual_intervention_required is False


@pytest.mark.asyncio
async def test_reconciler_reports_missing_state_without_creating_directories(
    tmp_path: Path,
) -> None:
    _, context = make_render_command_context()
    result = await LocalRenderReconciler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=_store(tmp_path),
        configuration=RenderingConfiguration(),
    ).reconcile(context=context)
    assert result.request_present is False
    assert result.execution_manifest_present is False
    assert result.recovery_safe is True
    assert result.stage_complete is False
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.asyncio
async def test_reconciler_reports_unexpected_output_without_renderer_invocation(
    tmp_path: Path,
) -> None:
    _, context = make_render_command_context()
    store = UnexpectedOutputStore(
        tmp_path,
        max_request_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    await store.write_request(context=context, request=request)
    result = await LocalRenderReconciler(
        source_reader=StaticVerifiedSourceReader(make_verified_source()),
        store=store,
        configuration=RenderingConfiguration(),
    ).reconcile(context=context)
    assert result.unexpected_output_file is True
    assert result.manual_intervention_required is True
    assert result.stage_complete is False
    assert not tuple(tmp_path.rglob("*.mp4"))


@pytest.mark.asyncio
async def test_reconciler_reports_missing_source_as_manual_intervention(
    tmp_path: Path,
) -> None:
    _, context = make_render_command_context()
    result = await LocalRenderReconciler(
        source_reader=MissingSourceReader(),
        store=_store(tmp_path),
        configuration=RenderingConfiguration(),
    ).reconcile(context=context)
    assert result.source_plan_present is False
    assert result.source_manifest_present is False
    assert result.recovery_safe is False
    assert result.manual_intervention_required is True
    assert result.stage_complete is False
    assert tuple(tmp_path.iterdir()) == ()
