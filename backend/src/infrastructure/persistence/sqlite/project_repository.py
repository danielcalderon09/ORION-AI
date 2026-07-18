"""SQLite implementation of Project Repository."""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import Column, String, DateTime, Text, create_engine
from sqlalchemy.orm import declarative_base

from backend.src.core.domain.entities.video_project import ProjectBrain, VideoProject
from backend.src.core.domain.repositories.i_project_repository import IProjectRepository
from backend.src.infrastructure.persistence.sqlite.connection import Database

Base = declarative_base()


class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    source_path = Column(Text, nullable=True)
    workspace_path = Column(Text, nullable=True)
    status = Column(String(50), default="CREATED")
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    brain_json = Column(Text, nullable=True)
    clips_json = Column(Text, default="[]")


class SQLiteProjectRepository(IProjectRepository):
    """SQLite implementation of project storage."""

    def __init__(self, database: Database):
        self.database = database
        self._ensure_table()

    def _ensure_table(self) -> None:
        # Create table if not exists
        Base.metadata.create_all(self.database.engine)

    def save(self, project: VideoProject) -> None:
        session = self.database.get_session()
        try:
            existing = session.query(ProjectModel).filter_by(id=str(project.project_id)).first()
            brain_json = json.dumps(self._brain_to_dict(project.brain)) if project.brain else None
            clips_json = json.dumps([self._clip_to_dict(c) for c in project.clips])

            if existing:
                existing.name = project.name
                existing.source_path = str(project.source_path) if project.source_path else None
                existing.workspace_path = str(project.workspace_path) if project.workspace_path else None
                existing.status = project.status.name if hasattr(project.status, "name") else str(project.status)
                existing.updated_at = project.updated_at
                existing.brain_json = brain_json
                existing.clips_json = clips_json
            else:
                model = ProjectModel(
                    id=str(project.project_id),
                    name=project.name,
                    source_path=str(project.source_path) if project.source_path else None,
                    workspace_path=str(project.workspace_path) if project.workspace_path else None,
                    status=project.status.name if hasattr(project.status, "name") else str(project.status),
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                    brain_json=brain_json,
                    clips_json=clips_json,
                )
                session.add(model)
            session.commit()
        finally:
            session.close()

    def get_by_id(self, project_id: UUID) -> VideoProject | None:
        session = self.database.get_session()
        try:
            model = session.query(ProjectModel).filter_by(id=str(project_id)).first()
            if not model:
                return None
            return self._model_to_project(model)
        finally:
            session.close()

    def get_all(self) -> list[VideoProject]:
        session = self.database.get_session()
        try:
            models = session.query(ProjectModel).all()
            return [self._model_to_project(m) for m in models]
        finally:
            session.close()

    def delete(self, project_id: UUID) -> None:
        session = self.database.get_session()
        try:
            session.query(ProjectModel).filter_by(id=str(project_id)).delete()
            session.commit()
        finally:
            session.close()

    def _brain_to_dict(self, brain) -> dict:
        if not brain:
            return {}
        return {
            "project_id": str(brain.project_id),
            "project_name": brain.project_name,
            "features_index": brain.features_index,
            "narrative_memory": brain.narrative_memory.__dict__ if brain.narrative_memory else {},
            "director_memory": brain.director_memory.__dict__ if brain.director_memory else {},
            "user_preferences": brain.user_preferences.__dict__ if brain.user_preferences else {},
        }

    def _clip_to_dict(self, clip) -> dict:
        return {
            "clip_id": str(clip.clip_id),
            "start": clip.temporal_range.start_seconds if clip.temporal_range else None,
            "end": clip.temporal_range.end_seconds if clip.temporal_range else None,
            "status": clip.status,
        }

    def _model_to_project(self, model) -> VideoProject:
        from datetime import datetime
        from backend.src.core.domain.entities.video_project import ProjectStatus

        status = ProjectStatus.CREATED
        try:
            status = ProjectStatus[model.status]
        except KeyError:
            pass

        return VideoProject(
            project_id=UUID(model.id),
            name=model.name,
            source_path=Path(model.source_path) if model.source_path else None,
            workspace_path=Path(model.workspace_path) if model.workspace_path else None,
            status=status,
            created_at=model.created_at or datetime.utcnow(),
            updated_at=model.updated_at or datetime.utcnow(),
        )
