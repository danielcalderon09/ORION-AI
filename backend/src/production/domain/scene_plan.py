"""Scene planning contract."""

from uuid import UUID, uuid4

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import AssetType, MotionType, TransitionType


class ScenePlan(ContractModel):
    """Creative intent for one ordered scene before assets are acquired."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    scene_id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    narration_text: str = Field(min_length=1)
    visual_description: str = Field(min_length=1)
    asset_query: str = Field(min_length=1)
    asset_type: AssetType
    motion: MotionType
    transition: TransitionType
    on_screen_text: str | None = None
