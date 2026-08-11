"""Provider-neutral deterministic total job cost accounting contracts."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel


class JobCostCategory(StrEnum):
    SCRIPTING = "scripting"
    SPEECH = "speech"
    NARRATION_FITTING = "narration_fitting"
    IMAGES = "images"
    VIDEO = "video"


class JobCostSource(StrEnum):
    REPORTED = "reported"
    ESTIMATED_FALLBACK = "estimated_fallback"


class ProviderCostRecord(ContractModel):
    """One real provider submission, identified independently of stage recovery."""

    category: JobCostCategory
    request_identity: str = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=100)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    reported_cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=24, decimal_places=9
    )
    accounted_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    cost_source: JobCostSource

    @field_validator(
        "estimated_cost_usd", "reported_cost_usd", "accounted_cost_usd", mode="before"
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("job cost money must not use float")
        return value

    @model_validator(mode="after")
    def validate_cost_source(self) -> ProviderCostRecord:
        expected = (
            self.reported_cost_usd
            if self.reported_cost_usd is not None
            else self.estimated_cost_usd
        )
        if self.accounted_cost_usd != expected:
            raise ValueError("accounted job cost differs from its source")
        if (self.reported_cost_usd is not None) != (
            self.cost_source is JobCostSource.REPORTED
        ):
            raise ValueError("job cost source differs from reported cost presence")
        return self


class CostCategorySummary(ContractModel):
    category: JobCostCategory
    request_count: int = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    reported_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    accounted_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    estimated_fallback_cost_usd: Decimal = Field(
        ge=0, max_digits=24, decimal_places=9
    )
    reported_cost_request_count: int = Field(ge=0)
    estimated_fallback_request_count: int = Field(ge=0)
    reported_cost_coverage_percent: Decimal = Field(ge=0, le=100, decimal_places=2)
    fully_reported: bool

    @field_validator(
        "estimated_cost_usd",
        "reported_cost_usd",
        "accounted_cost_usd",
        "estimated_fallback_cost_usd",
        "reported_cost_coverage_percent",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("job cost summary must not use float")
        return value

    @model_validator(mode="after")
    def validate_summary(self) -> CostCategorySummary:
        if (
            self.reported_cost_request_count + self.estimated_fallback_request_count
            != self.request_count
        ):
            raise ValueError("job cost source counts differ from request count")
        if self.accounted_cost_usd != (
            self.reported_cost_usd + self.estimated_fallback_cost_usd
        ):
            raise ValueError("accounted category cost differs from source subtotals")
        expected_coverage = _coverage(
            self.reported_cost_request_count, self.request_count
        )
        if self.reported_cost_coverage_percent != expected_coverage:
            raise ValueError("reported category coverage differs")
        if self.fully_reported != (self.estimated_fallback_request_count == 0):
            raise ValueError("category fully-reported flag differs")
        return self


class ProviderCostSummary(ContractModel):
    provider: str = Field(min_length=1, max_length=100)
    request_count: int = Field(ge=0)
    accounted_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)

    @field_validator("accounted_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("provider cost must not use float")
        return value


class VisualCostAudit(ContractModel):
    budget_estimated_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    accounted_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    cost_delta_usd: Decimal = Field(max_digits=24, decimal_places=9)
    budget_exceeded: bool
    image_budget_respected: bool
    video_budget_respected: bool
    total_visual_budget_respected: bool

    @field_validator(
        "budget_estimated_cost_usd", "accounted_cost_usd", "cost_delta_usd", mode="before"
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("visual cost audit must not use float")
        return value

    @model_validator(mode="after")
    def validate_delta(self) -> VisualCostAudit:
        if self.cost_delta_usd != self.accounted_cost_usd - self.budget_estimated_cost_usd:
            raise ValueError("visual cost delta differs")
        if self.budget_exceeded != (not self.total_visual_budget_respected):
            raise ValueError("visual budget exceeded flag differs")
        return self


class ProductionJobCostSummary(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    categories: tuple[CostCategorySummary, ...] = Field(min_length=5, max_length=5)
    providers: tuple[ProviderCostSummary, ...] = ()
    total_request_count: int = Field(ge=0)
    total_estimated_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    total_reported_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    total_accounted_cost_usd: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    total_estimated_fallback_cost_usd: Decimal = Field(
        ge=0, max_digits=24, decimal_places=9
    )
    total_reported_cost_request_count: int = Field(ge=0)
    total_estimated_fallback_request_count: int = Field(ge=0)
    reported_cost_coverage_percent: Decimal = Field(ge=0, le=100, decimal_places=2)
    fully_reported: bool
    visual_cost_audit: VisualCostAudit | None = None
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "total_estimated_cost_usd",
        "total_reported_cost_usd",
        "total_accounted_cost_usd",
        "total_estimated_fallback_cost_usd",
        "reported_cost_coverage_percent",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("total job cost must not use float")
        return value

    def calculated_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        return hashlib.sha256(_canonical_json(payload)).hexdigest()

    def category(self, category: JobCostCategory) -> CostCategorySummary:
        return next(item for item in self.categories if item.category is category)

    @model_validator(mode="after")
    def validate_totals(self) -> ProductionJobCostSummary:
        expected_categories = tuple(JobCostCategory)
        if tuple(item.category for item in self.categories) != expected_categories:
            raise ValueError("job cost categories are not canonical")
        if tuple(item.provider for item in self.providers) != tuple(
            sorted(item.provider for item in self.providers)
        ):
            raise ValueError("job cost providers are not canonical")
        if self.total_request_count != sum(item.request_count for item in self.categories):
            raise ValueError("total job request count differs")
        if self.total_estimated_cost_usd != sum(
            (item.estimated_cost_usd for item in self.categories), Decimal("0")
        ):
            raise ValueError("total estimated job cost differs")
        if self.total_reported_cost_usd != sum(
            (item.reported_cost_usd for item in self.categories), Decimal("0")
        ):
            raise ValueError("total reported job cost differs")
        if self.total_accounted_cost_usd != sum(
            (item.accounted_cost_usd for item in self.categories), Decimal("0")
        ):
            raise ValueError("total accounted job cost differs")
        if self.total_estimated_fallback_cost_usd != (
            self.total_accounted_cost_usd - self.total_reported_cost_usd
        ):
            raise ValueError("total fallback job cost differs")
        expected_reported_count = sum(
            item.reported_cost_request_count for item in self.categories
        )
        expected_fallback_count = sum(
            item.estimated_fallback_request_count for item in self.categories
        )
        if self.total_reported_cost_request_count != expected_reported_count:
            raise ValueError("total reported request count differs")
        if self.total_estimated_fallback_request_count != expected_fallback_count:
            raise ValueError("total fallback request count differs")
        if sum(item.request_count for item in self.providers) != self.total_request_count:
            raise ValueError("provider request count differs from total")
        if sum(
            (item.accounted_cost_usd for item in self.providers), Decimal("0")
        ) != self.total_accounted_cost_usd:
            raise ValueError("provider accounted cost differs from total")
        if self.reported_cost_coverage_percent != _coverage(
            self.total_reported_cost_request_count, self.total_request_count
        ):
            raise ValueError("total reported coverage differs")
        if self.fully_reported != (self.total_estimated_fallback_request_count == 0):
            raise ValueError("total fully-reported flag differs")
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("job cost summary fingerprint differs")
        return self


def build_job_cost_summary(
    *,
    job_id: UUID,
    records: tuple[ProviderCostRecord, ...],
    visual_cost_audit: VisualCostAudit | None = None,
) -> ProductionJobCostSummary:
    deduplicated: dict[tuple[JobCostCategory, str], ProviderCostRecord] = {}
    for record in records:
        key = (record.category, record.request_identity)
        existing = deduplicated.get(key)
        if existing is not None and existing != record:
            raise ValueError("durable provider request identity has conflicting costs")
        deduplicated[key] = record
    canonical = tuple(
        sorted(
            deduplicated.values(),
            key=lambda item: (item.category.value, item.request_identity),
        )
    )
    categories = tuple(
        _summarize_category(category, canonical) for category in JobCostCategory
    )
    providers = tuple(
        ProviderCostSummary(
            provider=provider,
            request_count=sum(item.provider == provider for item in canonical),
            accounted_cost_usd=sum(
                (item.accounted_cost_usd for item in canonical if item.provider == provider),
                Decimal("0"),
            ),
        )
        for provider in sorted({item.provider for item in canonical})
    )
    reported_count = sum(item.reported_cost_request_count for item in categories)
    fallback_count = sum(item.estimated_fallback_request_count for item in categories)
    provisional = ProductionJobCostSummary.model_construct(
        job_id=job_id,
        categories=categories,
        providers=providers,
        total_request_count=len(canonical),
        total_estimated_cost_usd=sum(
            (item.estimated_cost_usd for item in categories), Decimal("0")
        ),
        total_reported_cost_usd=sum(
            (item.reported_cost_usd for item in categories), Decimal("0")
        ),
        total_accounted_cost_usd=sum(
            (item.accounted_cost_usd for item in categories), Decimal("0")
        ),
        total_estimated_fallback_cost_usd=sum(
            (item.estimated_fallback_cost_usd for item in categories), Decimal("0")
        ),
        total_reported_cost_request_count=reported_count,
        total_estimated_fallback_request_count=fallback_count,
        reported_cost_coverage_percent=_coverage(reported_count, len(canonical)),
        fully_reported=fallback_count == 0,
        visual_cost_audit=visual_cost_audit,
        fingerprint="0" * 64,
    )
    return ProductionJobCostSummary.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "fingerprint": provisional.calculated_fingerprint(),
        }
    )


def _summarize_category(
    category: JobCostCategory,
    records: tuple[ProviderCostRecord, ...],
) -> CostCategorySummary:
    selected = tuple(item for item in records if item.category is category)
    reported_count = sum(item.reported_cost_usd is not None for item in selected)
    fallback_count = len(selected) - reported_count
    reported = sum(
        (item.reported_cost_usd for item in selected if item.reported_cost_usd is not None),
        Decimal("0"),
    )
    fallback = sum(
        (item.accounted_cost_usd for item in selected if item.reported_cost_usd is None),
        Decimal("0"),
    )
    return CostCategorySummary(
        category=category,
        request_count=len(selected),
        estimated_cost_usd=sum(
            (item.estimated_cost_usd for item in selected), Decimal("0")
        ),
        reported_cost_usd=reported,
        accounted_cost_usd=reported + fallback,
        estimated_fallback_cost_usd=fallback,
        reported_cost_request_count=reported_count,
        estimated_fallback_request_count=fallback_count,
        reported_cost_coverage_percent=_coverage(reported_count, len(selected)),
        fully_reported=fallback_count == 0,
    )


def _coverage(reported: int, total: int) -> Decimal:
    if total == 0:
        return Decimal("100.00")
    return ((Decimal(reported) * Decimal(100)) / Decimal(total)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
