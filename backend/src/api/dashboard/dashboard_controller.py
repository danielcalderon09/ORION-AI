"""Dashboard API for quality metrics and observability."""

from fastapi import APIRouter

from backend.src.infrastructure.observability.observability_stack import ObservabilityStack
from backend.src.infrastructure.profiler.performance_profiler import PerformanceProfiler
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.persistence.sqlite.project_repository import SQLiteProjectRepository
from backend.src.infrastructure.persistence.sqlite.connection import Database

router = APIRouter()

# Shared observability instance (in production this would be injected)
_observability = ObservabilityStack()
_profiler = PerformanceProfiler()


@router.get("/health")
async def health_check():
    """System health status with per-subsystem checks."""
    health = _observability.get_health()
    return {
        "status": health.get("status", "unknown"),
        "timestamp": health.get("timestamp"),
        "subsystems": {
            "api": {"status": "healthy"},
            "pipeline": {"status": health.get("status"), "issues": health.get("issues", [])},
            "memory": {
                "ram_percent": health.get("ram_percent"),
                "vram_percent": health.get("vram_used_mb"),
            },
            "disk": {"free_gb": health.get("disk_free_gb")},
        },
    }


@router.get("/metrics")
async def dashboard_metrics():
    """Aggregated quality metrics across all projects."""
    try:
        db = Database(settings.ORION_HOME / "orion.db")
        repo = SQLiteProjectRepository(db)
        projects = repo.get_all()
    except Exception:
        projects = []

    total_projects = len(projects)
    total_clips = sum(len(p.clips) for p in projects)
    exported_clips = sum(1 for p in projects for c in p.clips if c.status == "exported")

    # Quality KPIs
    avg_clips_per_project = total_clips / max(total_projects, 1)
    export_success_rate = exported_clips / max(total_clips, 1)

    # Agent performance
    agent_summary = _observability.get_agent_summary()

    # Pipeline performance
    profiler_summary = _profiler.get_summary()

    return {
        "projects": {
            "total": total_projects,
            "total_clips": total_clips,
            "exported_clips": exported_clips,
            "avg_clips_per_project": round(avg_clips_per_project, 2),
            "export_success_rate": round(export_success_rate, 3),
        },
        "agents": agent_summary,
        "pipeline": profiler_summary,
        "system": _observability.get_health(),
    }


@router.get("/system/recent")
async def system_recent_samples(n: int = 10):
    """Recent system resource samples."""
    return {
        "samples": _observability.get_recent_samples(n),
    }


@router.get("/pipeline/cache")
async def pipeline_cache_stats():
    """Pipeline cache statistics."""
    from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
    cache = PipelineCache()
    return cache.get_stats()


@router.get("/profiles")
async def list_profiles():
    """List available configuration profiles."""
    from backend.src.infrastructure.config_profiles.config_profile_manager import ConfigProfileManager
    mgr = ConfigProfileManager()
    return {
        "profiles": mgr.list_profiles(),
        "defaults": {
            "new_users": "balanced",
            "fast_export": "fast",
            "max_quality": "quality",
        },
    }


@router.get("/version")
async def version_info():
    """Version information for reproducibility."""
    from backend.src.infrastructure.versioning.versioning_manager import VersioningManager
    vm = VersioningManager()
    vm.auto_detect_versions()
    return {
        "orion_version": settings.APP_VERSION,
        "components": [
            {"id": c.component_id, "version": c.version, "source": c.source}
            for c in vm._component_versions.values()
        ],
    }
