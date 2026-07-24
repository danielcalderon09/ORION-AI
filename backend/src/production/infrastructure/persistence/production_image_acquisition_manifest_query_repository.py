"""Read-only image acquisition manifest and SOURCE_IMAGE artifact queries."""

from uuid import UUID

from sqlalchemy import select

from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.infrastructure.persistence.models import ArtifactRecord
from backend.src.production.infrastructure.persistence.session import (
    ProductionSessionFactory,
)
from backend.src.production.video_clip_generation.ports import (
    ImageManifestArtifactCandidate,
    InputArtifactIdentity,
    SourceImageArtifactCandidate,
)


class SQLAlchemyImageAcquisitionManifestQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def list_candidates(
        self, *, job_id: UUID
    ) -> tuple[ImageManifestArtifactCandidate, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.job_id == str(job_id),
                    ArtifactRecord.artifact_type
                    == ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST.value,
                    ArtifactRecord.status == ArtifactStatus.READY.value,
                )
                .order_by(
                    ArtifactRecord.created_at.desc(),
                    ArtifactRecord.artifact_id.desc(),
                )
            )
            return tuple(
                ImageManifestArtifactCandidate(
                    artifact_id=UUID(record.artifact_id),
                    job_id=UUID(record.job_id),
                    artifact_type=ArtifactType(record.artifact_type),
                    relative_path=record.relative_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    provider=record.provider,
                    model_version=record.model_version,
                    created_at=record.created_at,
                    metadata=record.metadata_json,
                )
                for record in records
            )

    def list_input_artifacts(
        self, *, artifact_ids: tuple[UUID, ...]
    ) -> dict[UUID, InputArtifactIdentity]:
        if not artifact_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    ArtifactRecord.artifact_id,
                    ArtifactRecord.job_id,
                    ArtifactRecord.artifact_type,
                ).where(
                    ArtifactRecord.artifact_id.in_(
                        str(artifact_id) for artifact_id in artifact_ids
                    ),
                )
            )
            result: dict[UUID, InputArtifactIdentity] = {}
            for artifact_id, artifact_job_id, artifact_type in rows:
                try:
                    identifier = UUID(artifact_id)
                    result[identifier] = InputArtifactIdentity(
                        artifact_id=identifier,
                        job_id=UUID(artifact_job_id),
                        artifact_type=ArtifactType(artifact_type),
                    )
                except ValueError:
                    continue
            return result

    def get_source_image(
        self, *, job_id: UUID, artifact_id: UUID
    ) -> SourceImageArtifactCandidate | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(ArtifactRecord).where(
                    ArtifactRecord.job_id == str(job_id),
                    ArtifactRecord.artifact_id == str(artifact_id),
                    ArtifactRecord.artifact_type == ArtifactType.SOURCE_IMAGE.value,
                    ArtifactRecord.status == ArtifactStatus.READY.value,
                )
            )
            if record is None:
                return None
            return SourceImageArtifactCandidate(
                artifact_id=UUID(record.artifact_id),
                job_id=UUID(record.job_id),
                artifact_type=ArtifactType(record.artifact_type),
                relative_path=record.relative_path,
                mime_type=record.mime_type,
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                width=record.width,
                height=record.height,
                metadata=record.metadata_json,
            )
