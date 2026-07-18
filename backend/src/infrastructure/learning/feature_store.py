"""Filesystem-based Feature Store with SQLite registry."""

import json
import pickle
from pathlib import Path
from uuid import UUID

from backend.src.core.domain.repositories.i_project_repository import IFeatureStore
from backend.src.infrastructure.persistence.sqlite.connection import Base, Database
from sqlalchemy import Column, String, Text, create_engine


class FeatureRegistryModel(Base):
    __tablename__ = "feature_registry"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), nullable=False)
    agent_id = Column(String(100), nullable=False)
    feature_name = Column(String(255), nullable=False)
    version = Column(String(20), default="1.0")
    file_path = Column(Text, nullable=False)
    format_type = Column(String(20), default="json")  # json, pickle, npy, parquet


class FileSystemFeatureStore(IFeatureStore):
    """Stores features on disk and indexes them in SQLite."""

    def __init__(self, base_path: Path, database: Database | None = None):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.database = database
        if database:
            Base.metadata.create_all(database.engine)

    def _project_dir(self, project_id: UUID) -> Path:
        path = self.base_path / str(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        data: object,
        version: str = "1.0",
    ) -> str:
        project_dir = self._project_dir(project_id)
        agent_dir = project_dir / agent_id
        agent_dir.mkdir(exist_ok=True)

        file_name = f"{feature_name}_v{version}.json"
        file_path = agent_dir / file_name

        # Determine serialization format
        if isinstance(data, (dict, list)):
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
            format_type = "json"
        else:
            pickle_path = agent_dir / f"{feature_name}_v{version}.pkl"
            with open(pickle_path, "wb") as f:
                pickle.dump(data, f)
            file_path = pickle_path
            format_type = "pickle"

        # Update registry if database available
        if self.database:
            self._update_registry(project_id, agent_id, feature_name, version, str(file_path), format_type)

        return str(file_path)

    def load(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        version: str = "1.0",
    ) -> object:
        project_dir = self._project_dir(project_id)
        json_path = project_dir / agent_id / f"{feature_name}_v{version}.json"
        pickle_path = project_dir / agent_id / f"{feature_name}_v{version}.pkl"

        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        elif pickle_path.exists():
            with open(pickle_path, "rb") as f:
                return pickle.load(f)
        else:
            raise FileNotFoundError(f"Feature {feature_name} v{version} not found for agent {agent_id}")

    def exists(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        version: str = "1.0",
    ) -> bool:
        project_dir = self._project_dir(project_id)
        json_path = project_dir / agent_id / f"{feature_name}_v{version}.json"
        pickle_path = project_dir / agent_id / f"{feature_name}_v{version}.pkl"
        return json_path.exists() or pickle_path.exists()

    def list_features(self, project_id: UUID) -> list[dict]:
        project_dir = self._project_dir(project_id)
        features = []
        if not project_dir.exists():
            return features
        for agent_dir in project_dir.iterdir():
            if agent_dir.is_dir():
                for file in agent_dir.iterdir():
                    features.append({
                        "agent_id": agent_dir.name,
                        "file": file.name,
                        "path": str(file),
                    })
        return features

    def _update_registry(self, project_id: UUID, agent_id: str, feature_name: str, version: str, file_path: str, format_type: str) -> None:
        if not self.database:
            return
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=self.database.engine)
        session = Session()
        try:
            # Delete existing
            session.query(FeatureRegistryModel).filter_by(
                project_id=str(project_id),
                agent_id=agent_id,
                feature_name=feature_name,
                version=version,
            ).delete()
            # Insert new
            model = FeatureRegistryModel(
                id=f"{project_id}:{agent_id}:{feature_name}:{version}",
                project_id=str(project_id),
                agent_id=agent_id,
                feature_name=feature_name,
                version=version,
                file_path=file_path,
                format_type=format_type,
            )
            session.add(model)
            session.commit()
        finally:
            session.close()
