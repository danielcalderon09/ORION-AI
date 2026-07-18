"""Deterministic PlanningProvider fixtures."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.planning.models import PlanningJobConfiguration
from backend.src.production.planning.ports import PlanningProviderRequest

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def planning_request() -> PlanningProviderRequest:
    return PlanningProviderRequest(
        job_id=UUID("10000000-0000-4000-8000-000000000501"),
        prompt="Explain how a solar eclipse works",
        configuration=PlanningJobConfiguration(
            language="en",
            target_duration_seconds=40,
            aspect_ratio="9:16",
            scene_count_hint=4,
        ),
        target_duration_seconds=40,
        language="en",
        aspect_ratio="9:16",
        correlation_id=UUID("30000000-0000-4000-8000-000000000501"),
        attempt_number=1,
    )
