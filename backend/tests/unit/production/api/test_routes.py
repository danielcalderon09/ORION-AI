"""HTTP behavior and durable Production Job use-case tests."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update

from backend.src.production.composition.container import ProductionContainer
from backend.src.production.infrastructure.persistence.models import ProductionJobRecord

JOBS = "/api/v1/production/jobs"


@pytest.mark.asyncio
async def test_create_get_list_events_and_artifacts(
    production_client: httpx.AsyncClient,
) -> None:
    response = await production_client.post(
        JOBS,
        json={"prompt": "  Un video   vertical ", "configuration": {"style": "calm"}},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["prompt"] == "Un video vertical"
    assert created["status"] == "queued"
    assert created["configuration"] == {"style": "calm"}

    job_id = created["job_id"]
    fetched = await production_client.get(f"{JOBS}/{job_id}")
    assert fetched.status_code == 200
    listing = await production_client.get(JOBS, params={"status": "queued"})
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["job_id"] == job_id
    events = await production_client.get(f"{JOBS}/{job_id}/events")
    assert [event["event_type"] for event in events.json()["items"]] == [
        "production_job_queued"
    ]
    artifacts = await production_client.get(f"{JOBS}/{job_id}/artifacts")
    assert artifacts.json() == {"items": []}


@pytest.mark.asyncio
async def test_create_request_validation(production_client: httpx.AsyncClient) -> None:
    assert (await production_client.post(JOBS, json={"prompt": "   "})).status_code == 422
    assert (
        await production_client.post(JOBS, json={"prompt": "ok", "status": "running"})
    ).status_code == 422
    assert (
        await production_client.post(
            JOBS,
            json={"prompt": "ok", "configuration": {"api_key": "secret"}},
        )
    ).status_code == 422
    assert (
        await production_client.post(
            JOBS,
            json={"prompt": "ok", "configuration": {"source": "C:\\private\\x"}},
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_create_is_durably_idempotent(production_client: httpx.AsyncClient) -> None:
    payload = {"prompt": "same", "client_request_id": "desktop:request-1"}
    first = await production_client.post(JOBS, json=payload)
    second = await production_client.post(JOBS, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["job_id"] == second.json()["job_id"]
    conflict = await production_client.post(
        JOBS,
        json={"prompt": "different", "client_request_id": "desktop:request-1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "production_conflict"


@pytest.mark.asyncio
async def test_concurrent_create_with_same_request_id_returns_one_job(
    production_client: httpx.AsyncClient,
) -> None:
    payload = {"prompt": "concurrent", "client_request_id": "desktop:concurrent"}
    first, second = await asyncio.gather(
        production_client.post(JOBS, json=payload),
        production_client.post(JOBS, json=payload),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["job_id"] == second.json()["job_id"]


@pytest.mark.asyncio
async def test_get_missing_and_pagination_validation(
    production_client: httpx.AsyncClient,
) -> None:
    assert (await production_client.get(f"{JOBS}/{uuid4()}")).status_code == 404
    assert (await production_client.get(JOBS, params={"limit": 101})).status_code == 422
    assert (await production_client.get(JOBS, params={"offset": -1})).status_code == 422


@pytest.mark.asyncio
async def test_cancel_is_durable_and_idempotent(
    production_client: httpx.AsyncClient,
) -> None:
    created = (await production_client.post(JOBS, json={"prompt": "cancel"})).json()
    endpoint = f"{JOBS}/{created['job_id']}/cancel"
    first = await production_client.post(endpoint)
    second = await production_client.post(endpoint)
    assert first.status_code == second.status_code == 200
    assert first.json()["job"]["status"] == "cancel_requested"
    assert second.json()["idempotent"] is True
    events = (await production_client.get(f"{JOBS}/{created['job_id']}/events")).json()
    assert [item["event_type"] for item in events["items"]] == [
        "production_job_queued",
        "production_cancellation_requested",
    ]


@pytest.mark.asyncio
async def test_worker_finalizes_requested_cancellation(
    production_client: httpx.AsyncClient,
    production_app: tuple[object, ProductionContainer],
) -> None:
    created = (await production_client.post(JOBS, json={"prompt": "cancel worker"})).json()
    await production_client.post(f"{JOBS}/{created['job_id']}/cancel")
    _, container = production_app
    await container.worker.run_until_idle(max_cycles=5)
    current = await production_client.get(f"{JOBS}/{created['job_id']}")
    assert current.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_concurrent_cancel_does_not_duplicate_event(
    production_client: httpx.AsyncClient,
) -> None:
    created = (await production_client.post(JOBS, json={"prompt": "race cancel"})).json()
    endpoint = f"{JOBS}/{created['job_id']}/cancel"
    responses = await asyncio.gather(
        production_client.post(endpoint), production_client.post(endpoint)
    )
    assert [response.status_code for response in responses] == [200, 200]
    events = (await production_client.get(f"{JOBS}/{created['job_id']}/events")).json()
    cancellation_events = [
        item for item in events["items"]
        if item["event_type"] == "production_cancellation_requested"
    ]
    assert len(cancellation_events) == 1


@pytest.mark.asyncio
async def test_manual_retry_from_failed_is_durable_and_idempotent(
    production_client: httpx.AsyncClient,
    production_app: tuple[object, ProductionContainer],
) -> None:
    created = (await production_client.post(JOBS, json={"prompt": "retry"})).json()
    _, container = production_app
    with container.engine.begin() as connection:
        connection.execute(
            update(ProductionJobRecord)
            .where(ProductionJobRecord.job_id == created["job_id"])
            .values(status="failed", error_code="simulated", row_version=2)
        )
    endpoint = f"{JOBS}/{created['job_id']}/retry"
    first = await production_client.post(endpoint)
    second = await production_client.post(endpoint)
    assert first.status_code == second.status_code == 200
    assert first.json()["job"]["status"] == "queued"
    assert second.json()["idempotent"] is True


@pytest.mark.asyncio
async def test_simulated_pipeline_completes_through_api(
    production_client: httpx.AsyncClient,
    production_app: tuple[object, ProductionContainer],
) -> None:
    created = (
        await production_client.post(
            JOBS,
            json={"prompt": "pipeline", "generate_clips_after_render": True},
        )
    ).json()
    _, container = production_app
    await container.worker.run_until_idle(max_cycles=40)
    final = await production_client.get(f"{JOBS}/{created['job_id']}")
    assert final.json()["status"] == "completed"
    artifacts = await production_client.get(f"{JOBS}/{created['job_id']}/artifacts")
    assert artifacts.status_code == 200
    assert all(not item["relative_path"].startswith("/") for item in artifacts.json()["items"])
