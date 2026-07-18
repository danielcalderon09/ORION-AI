"""Local, lease-aware runtime composition for the production pipeline."""

from backend.src.production.runtime.blocking_executor import (
    ImmediateRuntimeBlockingExecutor,
    RuntimeBlockingExecutor,
    ThreadedRuntimeBlockingExecutor,
)
from backend.src.production.runtime.claimed_job_processor import (
    ClaimedJobProcessor,
    ProductionRuntimeError,
)
from backend.src.production.runtime.context import (
    StageContext,
    StageContextFactory,
    StageContextMismatchError,
)
from backend.src.production.runtime.decision_persister import (
    ImmediateRuntimeDecisionPersister,
    RuntimeDecisionPersister,
    ThreadedRuntimeDecisionPersister,
)
from backend.src.production.runtime.executor import (
    ProductionExecutor,
    StageExecutionContractError,
)
from backend.src.production.runtime.handler_factory import (
    create_handler_registry,
    create_simulated_handler_registry,
)
from backend.src.production.runtime.heartbeat import ProductionHeartbeat
from backend.src.production.runtime.job_dispatcher import (
    StageHandlerNotFoundError,
    StageHandlerRegistrationError,
    StageHandlerRegistry,
)
from backend.src.production.runtime.leases import (
    LeaseRepository,
    ProductionLeaseError,
    ProductionLeaseManager,
    ProductionLeaseOwnershipError,
    SQLAlchemyLeaseRepository,
)
from backend.src.production.runtime.recovery import ProductionRecoveryService
from backend.src.production.runtime.runtime_models import (
    ProductionLease,
    RuntimeRecoveryResult,
    RuntimeRetryCandidate,
    StageExecutionOutput,
    WorkerRunResult,
)
from backend.src.production.runtime.runtime_state_reader import (
    MultiplePendingStageCommandsError,
    RuntimeStateIntegrityError,
    RuntimeStateReader,
)
from backend.src.production.runtime.worker import ProductionWorker
from backend.src.production.runtime.worker_loop import (
    ProductionWorkerLoop,
    ProductionWorkerLoopError,
)

__all__ = [
    "ClaimedJobProcessor",
    "ImmediateRuntimeBlockingExecutor",
    "ImmediateRuntimeDecisionPersister",
    "LeaseRepository",
    "MultiplePendingStageCommandsError",
    "ProductionExecutor",
    "ProductionHeartbeat",
    "ProductionLease",
    "ProductionLeaseError",
    "ProductionLeaseManager",
    "ProductionLeaseOwnershipError",
    "ProductionRecoveryService",
    "ProductionRuntimeError",
    "ProductionWorker",
    "ProductionWorkerLoop",
    "ProductionWorkerLoopError",
    "RuntimeRecoveryResult",
    "RuntimeRetryCandidate",
    "RuntimeBlockingExecutor",
    "RuntimeDecisionPersister",
    "RuntimeStateIntegrityError",
    "RuntimeStateReader",
    "SQLAlchemyLeaseRepository",
    "StageContext",
    "StageContextFactory",
    "StageContextMismatchError",
    "StageExecutionContractError",
    "StageExecutionOutput",
    "StageHandlerNotFoundError",
    "StageHandlerRegistrationError",
    "StageHandlerRegistry",
    "ThreadedRuntimeBlockingExecutor",
    "ThreadedRuntimeDecisionPersister",
    "WorkerRunResult",
    "create_simulated_handler_registry",
    "create_handler_registry",
]
