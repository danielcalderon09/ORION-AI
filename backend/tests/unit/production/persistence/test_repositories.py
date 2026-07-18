"""Repository behavior tests using isolated SQLite databases."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionIdempotencyConflictError,
)
from backend.src.production.infrastructure.persistence.models import ProductionJobRecord
from backend.src.production.infrastructure.persistence.repositories import (
    SQLAlchemyArtifactStore,
    SQLAlchemyProductionJobRepository,
)
from backend.tests.unit.production.persistence.factories import (
    ARTIFACT_ID,
    JOB_ID,
    NOW,
    make_artifact,
    make_job,
)


@pytest.mark.asyncio
async def test_job_repository_add_and_get(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
        job = make_job()

        await repository.add(job)
        session.commit()

        assert await repository.get(job.job_id) == job


@pytest.mark.asyncio
async def test_job_repository_rejects_duplicate_add(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
        job = make_job()
        await repository.add(job)
        session.commit()

        with pytest.raises(ProductionIdempotencyConflictError, match="already exists"):
            await repository.add(job)


@pytest.mark.asyncio
async def test_job_repository_save_increments_row_version(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
        job = make_job()
        await repository.add(job)
        session.commit()
        loaded = await repository.get(job.job_id)
        assert loaded is not None
        updated = loaded.model_copy(
            update={
                "status": ProductionJobStatus.QUEUED,
                "updated_at": NOW + timedelta(seconds=1),
            }
        )

        await repository.save(updated)
        session.commit()

        row_version = session.scalar(
            select(ProductionJobRecord.row_version).where(
                ProductionJobRecord.job_id == str(job.job_id)
            )
        )
        assert row_version == 2


@pytest.mark.asyncio
async def test_job_repository_detects_optimistic_lock_conflict(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as seed_session:
        seed_repository = SQLAlchemyProductionJobRepository(seed_session, clock=lambda: NOW)
        await seed_repository.add(make_job())
        seed_session.commit()

    session_one = session_factory()
    session_two = session_factory()
    try:
        repo_one = SQLAlchemyProductionJobRepository(session_one, clock=lambda: NOW)
        repo_two = SQLAlchemyProductionJobRepository(session_two, clock=lambda: NOW)
        first = await repo_one.get(JOB_ID)
        stale = await repo_two.get(JOB_ID)
        assert first is not None and stale is not None

        await repo_one.save(
            first.model_copy(
                update={
                    "status": ProductionJobStatus.QUEUED,
                    "updated_at": NOW + timedelta(seconds=1),
                }
            )
        )
        session_one.commit()

        with pytest.raises(ProductionConcurrencyError, match="stale production job"):
            await repo_two.save(
                stale.model_copy(
                    update={
                        "status": ProductionJobStatus.CANCEL_REQUESTED,
                        "updated_at": NOW + timedelta(seconds=2),
                    }
                )
            )
        session_two.rollback()
    finally:
        session_one.close()
        session_two.close()


@pytest.mark.asyncio
async def test_job_repository_lists_statuses_deterministically(production_database) -> None:
    _, session_factory = production_database
    ids = [
        UUID("10000000-0000-4000-8000-000000000003"),
        UUID("10000000-0000-4000-8000-000000000001"),
        UUID("10000000-0000-4000-8000-000000000002"),
    ]
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
        for job_id in ids:
            await repository.add(
                make_job(
                    job_id=job_id,
                    status=ProductionJobStatus.QUEUED,
                    stage=ProductionStage.CREATED,
                )
            )
        session.commit()

        jobs = await repository.list_by_status({ProductionJobStatus.QUEUED})

        assert [job.job_id for job in jobs] == sorted(ids)


async def add_job_for_artifacts(session, *, job_id: UUID = JOB_ID) -> None:
    repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
    await repository.add(make_job(job_id=job_id))


@pytest.mark.asyncio
async def test_artifact_store_save_get_and_idempotent_replay(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        await add_job_for_artifacts(session)
        store = SQLAlchemyArtifactStore(session, clock=lambda: NOW)
        artifact = make_artifact()

        first = await store.save(artifact)
        second = await store.save(artifact)
        session.commit()

        assert first == second == artifact
        assert await store.get(artifact.artifact_id) == artifact


@pytest.mark.asyncio
async def test_artifact_store_rejects_job_change(production_database) -> None:
    _, session_factory = production_database
    other_job_id = UUID("10000000-0000-4000-8000-000000000099")
    with session_factory() as session:
        await add_job_for_artifacts(session)
        store = SQLAlchemyArtifactStore(session, clock=lambda: NOW)
        await store.save(make_artifact())

        with pytest.raises(ProductionIdempotencyConflictError, match="cannot change job"):
            await store.save(make_artifact(job_id=other_job_id))


@pytest.mark.asyncio
async def test_artifact_store_rejects_path_conflict(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        await add_job_for_artifacts(session)
        store = SQLAlchemyArtifactStore(session, clock=lambda: NOW)
        await store.save(make_artifact())

        with pytest.raises(ProductionIdempotencyConflictError, match="path already belongs"):
            await store.save(
                make_artifact(
                    artifact_id=UUID("30000000-0000-4000-8000-000000000099")
                )
            )


@pytest.mark.asyncio
async def test_artifact_store_lists_deterministically(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        await add_job_for_artifacts(session)
        store = SQLAlchemyArtifactStore(session, clock=lambda: NOW)
        second = make_artifact(
            artifact_id=UUID("30000000-0000-4000-8000-000000000002"),
            relative_path="assets/z.png",
        )
        first = make_artifact(
            artifact_id=UUID("30000000-0000-4000-8000-000000000003"),
            relative_path="assets/a.png",
        )
        await store.save(second)
        await store.save(first)
        session.commit()

        artifacts = await store.list_for_job(JOB_ID)

        assert [artifact.relative_path for artifact in artifacts] == [
            "assets/a.png",
            "assets/z.png",
        ]
        assert {artifact.artifact_id for artifact in artifacts} != {ARTIFACT_ID}
