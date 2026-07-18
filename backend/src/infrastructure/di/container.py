"""Dependency Injection Container."""

from dependency_injector import containers, providers

from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.messaging.event_bus import EventBus
from backend.src.infrastructure.model_registry.capability_registry import CapabilityRegistry
from backend.src.infrastructure.persistence.sqlite.connection import Database
from backend.src.infrastructure.persistence.sqlite.project_repository import SQLiteProjectRepository
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
from backend.src.infrastructure.cognition.knowledge_graph_impl import InMemoryKnowledgeGraph
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite


class Container(containers.DeclarativeContainer):
    """DI Container for Orion AI."""

    # Core infrastructure singletons
    event_bus = providers.Singleton(EventBus)
    capability_registry = providers.Singleton(CapabilityRegistry)

    # Settings
    config = providers.Object(settings)

    # Database
    database = providers.Singleton(Database, db_path=settings.ORION_HOME / "orion.db")

    # Repositories
    project_repository = providers.Singleton(
        SQLiteProjectRepository, database=database
    )

    # Media
    media_processor = providers.Singleton(FFmpegMediaAdapter)

    # Storage
    feature_store = providers.Singleton(FileSystemFeatureStore, base_path=settings.ORION_HOME / "features")
    knowledge_graph = providers.Factory(InMemoryKnowledgeGraph)

    # Observability
    telemetry = providers.Singleton(TelemetryService, enabled=settings.TELEMETRY_ENABLED)
    benchmark = providers.Singleton(BenchmarkSuite, enabled=settings.BENCHMARK_ENABLED)
