"""Local, lease-aware runtime composition for the production pipeline."""

from backend.src.production.runtime.executor import (
    ProductionExecutor,
    StageExecutionContractError,
)
from backend.src.production.runtime.handler_factory import create_simulated_handler_registry
from backend.src.production.runtime.heartbeat import ProductionHeartbeat
from backend.src.production.runtime.job_dispatcher import (
    StageHandlerNotFoundError,
    StageHandlerRegistrationError,
    StageHandlerRegistry,
)
from backend.src.production.runtime.lease_manager import (
    ProductionLeaseError,
    ProductionLeaseManager,
    ProductionLeaseOwnershipError,
)
from backend.src.production.runtime.recovery import ProductionRecoveryService
from backend.src.production.runtime.runtime_models import (
    ProductionLease,
    RuntimeRecoveryResult,
    StageExecutionOutput,
    WorkerRunResult,
)
from backend.src.production.runtime.worker import ProductionRuntimeError, ProductionWorker

__all__ = [
    "ProductionExecutor",
    "ProductionHeartbeat",
    "ProductionLease",
    "ProductionLeaseError",
    "ProductionLeaseManager",
    "ProductionLeaseOwnershipError",
    "ProductionRecoveryService",
    "ProductionRuntimeError",
    "ProductionWorker",
    "RuntimeRecoveryResult",
    "StageExecutionContractError",
    "StageExecutionOutput",
    "StageHandlerNotFoundError",
    "StageHandlerRegistrationError",
    "StageHandlerRegistry",
    "WorkerRunResult",
    "create_simulated_handler_registry",
]
