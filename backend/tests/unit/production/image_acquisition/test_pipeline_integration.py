"""SQLite/workspace integration for the complete simulated production pipeline."""

import base64
import hashlib
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.models import (
    CreateProductionJobCommand,
)
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.image_acquisition.providers.openrouter_provider import (
    OpenRouterImageAcquisitionProvider,
)
from backend.src.production.infrastructure.persistence.models import (
    ArtifactRecord,
    ProductionBase,
)
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("image_provider_name", ["simulated", "openrouter"])
async def test_full_pipeline_acquires_durable_images_without_real_network(
    monkeypatch,
    tmp_path,
    image_provider_name,
) -> None:
    calls = 0

    def image_factory(**kwargs):
        async def respond(http_request):
            nonlocal calls
            calls += 1
            payload = json.loads(http_request.content)
            width, height = (
                int(value) for value in payload["size"].split("x")
            )
            stream = BytesIO()
            Image.new("RGB", (width, height), "navy").save(stream, "PNG")
            return httpx.Response(
                200,
                json={
                    "id": f"fake-image-{calls}",
                    "model": "openai/fake-image-model",
                    "data": [
                        {
                            "b64_json": base64.b64encode(
                                stream.getvalue()
                            ).decode(),
                            "media_type": "image/png",
                        }
                    ],
                },
                request=http_request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        kwargs["max_transport_attempts"] = 1
        return OpenRouterImageAcquisitionProvider(**kwargs, client=client)

    monkeypatch.setattr(
        "backend.src.production.composition.container."
        "load_openrouter_image_acquisition_provider",
        lambda: image_factory,
    )
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "production.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_IMAGE_ACQUISITION_PROVIDER=image_provider_name,
        ORION_IMAGE_ACQUISITION_MODEL=(
            "openai/fake-image-model"
            if image_provider_name == "openrouter"
            else ""
        ),
        ORION_IMAGE_ACQUISITION_API_KEY=(
            "fake-test-only" if image_provider_name == "openrouter" else None
        ),
    )
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    try:
        created = await container.create_job.execute(
            CreateProductionJobCommand(
                prompt="Explain a safe geometric skyline.",
                configuration={
                    "planning": {
                        "language": "en",
                        "target_duration_seconds": 10,
                        "scene_count_hint": 1,
                    },
                    "visual_asset_planning": {
                        "target_width": 64,
                        "target_height": 64,
                    },
                },
            )
        )
        await container.worker.run_until_idle(max_cycles=50)
        completed = await container.get_job.execute(created.job.job_id)
        assert completed.job.status is ProductionJobStatus.COMPLETED
        with container.engine.connect() as connection:
            rows = connection.execute(
                select(
                    ArtifactRecord.artifact_type,
                    ArtifactRecord.relative_path,
                    ArtifactRecord.sha256,
                    ArtifactRecord.metadata_json,
                ).where(ArtifactRecord.job_id == str(created.job.job_id))
            ).all()
            count_before = connection.scalar(
                select(func.count(ArtifactRecord.artifact_id)).where(
                    ArtifactRecord.job_id == str(created.job.job_id)
                )
            )
        images = [
            record
            for record in rows
            if record[0] == ArtifactType.SOURCE_IMAGE.value
        ]
        manifests = [
            record
            for record in rows
            if record[0]
            == ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST.value
        ]
        video_clips = [
            record
            for record in rows
            if record[0] == ArtifactType.SOURCE_VIDEO_CLIP.value
        ]
        video_manifests = [
            record
            for record in rows
            if record[0]
            == ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST.value
        ]
        assert len(images) == 1
        assert len(manifests) == 1
        assert len(video_clips) == 1
        assert len(video_manifests) == 1
        for record in (*images, *manifests, *video_clips, *video_manifests):
            target = settings.PROJECTS_DIR.joinpath(
                *record[1].split("/")
            )
            assert target.is_file()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record[2]
        image_target = settings.PROJECTS_DIR.joinpath(
            *images[0][1].split("/")
        )
        assert image_target.with_name(f"{image_target.name}.asset.json").is_file()
        video_target = settings.PROJECTS_DIR.joinpath(
            *video_clips[0][1].split("/")
        )
        assert video_target.with_name(f"{video_target.name}.asset.json").is_file()
        assert video_clips[0][3]["has_audio"] is False
        assert video_clips[0][3]["simulated"] is True
        assert "content" not in images[0][3]
        assert "base64" not in images[0][3]
        assert "prompt" not in images[0][3]
        await container.worker.run_until_idle(max_cycles=5)
        with container.engine.connect() as connection:
            count_after = connection.scalar(
                select(func.count(ArtifactRecord.artifact_id)).where(
                    ArtifactRecord.job_id == str(created.job.job_id)
                )
            )
        assert count_after == count_before
        assert calls == (1 if image_provider_name == "openrouter" else 0)
    finally:
        await container.aclose()
