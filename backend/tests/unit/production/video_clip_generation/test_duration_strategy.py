"""Provider duration selection remains capability-driven and fail-closed."""

import pytest

from backend.src.production.video_clip_generation.duration_strategy import (
    select_provider_duration,
)
from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoCapabilityError,
)


@pytest.mark.parametrize(
    ("planned", "selected"),
    [(3.0, 4), (4.0, 4), (4.1, 6), (6.0, 6), (7.9, 8)],
)
def test_selects_smallest_supported_covering_duration(planned: float, selected: int) -> None:
    assert select_provider_duration(planned, (8, 4, 6)) == selected


@pytest.mark.parametrize(("resolved", "selected"), ((4.25, 6), (5.0, 6), (6.5, 8)))
def test_audio_first_duration_selects_covering_provider_clip(
    resolved: float,
    selected: int,
) -> None:
    assert select_provider_duration(resolved, (4, 6, 8)) == selected


def test_fails_when_scene_exceeds_provider_maximum() -> None:
    with pytest.raises(OpenRouterVideoCapabilityError) as captured:
        select_provider_duration(8.1, (4, 6, 8))
    assert captured.value.diagnostic_code == "capability_duration_unsupported"
