"""Short-session query for planning artifact reconciliation."""

from sqlalchemy import select

from backend.src.production.domain.enums import ArtifactType
from backend.src.production.infrastructure.persistence.models import ArtifactRecord
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory


class SQLAlchemyRegisteredPlanningArtifactReader:
    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        *,
        artifact_types: frozenset[ArtifactType] = frozenset(
            {ArtifactType.PRODUCTION_PLAN}
        ),
    ) -> None:
        self._session_factory = session_factory
        self._artifact_types = artifact_types

    def list_registered_paths(self) -> frozenset[str]:
        with self._session_factory() as session:
            paths = session.scalars(
                select(ArtifactRecord.relative_path).where(
                    ArtifactRecord.artifact_type.in_(
                        item.value for item in self._artifact_types
                    )
                )
            )
            return frozenset(paths)
