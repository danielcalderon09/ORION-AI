"""Contracts for conservative planning artifact reconciliation."""

from typing import Protocol

from pydantic import Field

from backend.src.production.domain.base import ContractModel


class PlanningArtifactReconciliationReport(ContractModel):
    scanned: int = Field(default=0, ge=0)
    registered: int = Field(default=0, ge=0)
    orphaned: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)
    quarantined: int = Field(default=0, ge=0)
    skipped_recent: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    binary_scanned: int = Field(default=0, ge=0)
    binary_valid: int = Field(default=0, ge=0)
    binary_issues: int = Field(default=0, ge=0)


class PlanningArtifactReconciliationError(RuntimeError):
    """Workspace integrity could not be established safely."""

    def __init__(self, report: PlanningArtifactReconciliationReport) -> None:
        super().__init__("planning artifact reconciliation failed safely")
        self.report = report


class PlanningArtifactReconciler(Protocol):
    async def reconcile(self) -> PlanningArtifactReconciliationReport: ...


class RegisteredPlanningArtifactReader(Protocol):
    def list_registered_paths(self) -> frozenset[str]: ...
