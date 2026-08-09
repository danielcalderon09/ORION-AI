"""Remote-job state, bounded polling, and billable-request policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, DecimalException

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoCostPolicyError,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterRemoteStatus,
    OpenRouterVideoModelCapability,
)


class BillableVideoGenerationPolicy:
    """Fail-closed cost and duplicate-submission gate."""

    def __init__(
        self,
        *,
        allow_billable_requests: bool,
        max_estimated_cost_usd: Decimal,
    ) -> None:
        if not isinstance(max_estimated_cost_usd, Decimal):
            raise TypeError("maximum video cost must be Decimal")
        self._allowed = allow_billable_requests
        self._maximum = max_estimated_cost_usd

    def authorize(
        self,
        *,
        provider: str,
        capability: OpenRouterVideoModelCapability,
        duration_seconds: int,
        resolution: str,
        output_count: int,
        has_remote_job: bool,
        has_recoverable_clip: bool,
    ) -> tuple[Decimal, str]:
        if (
            not self._allowed
            or provider != "openrouter"
            or output_count != 1
            or has_remote_job
            or has_recoverable_clip
        ):
            raise OpenRouterVideoCostPolicyError(
                "billable video generation is not authorized",
                diagnostic_phase="cost_authorization",
                diagnostic_code=(
                    "billable_authorization_disabled"
                    if not self._allowed
                    else "cost_authorization_rejected"
                ),
                diagnostic_metadata={
                    "max_estimated_cost_usd": str(self._maximum),
                },
            )
        sku = f"per-video-second-{resolution}"
        price = capability.pricing_skus.get(sku)
        if price is None:
            sku = "per-video-second"
            price = capability.pricing_skus.get(sku)
        if price is None:
            raise OpenRouterVideoCostPolicyError(
                "OpenRouter video cost cannot be estimated safely",
                diagnostic_phase="pricing_discovery",
                diagnostic_code="pricing_sku_missing",
                diagnostic_metadata={
                    "pricing_sku": None,
                    "max_estimated_cost_usd": str(self._maximum),
                },
            )
        try:
            estimated = price * Decimal(duration_seconds)
        except (DecimalException, TypeError, ValueError) as exc:
            raise OpenRouterVideoCostPolicyError(
                "OpenRouter video cost cannot be estimated safely",
                diagnostic_phase="cost_estimation",
                diagnostic_code="cost_estimation_failed",
                diagnostic_metadata={
                    "pricing_sku": sku,
                    "max_estimated_cost_usd": str(self._maximum),
                },
            ) from exc
        if estimated > self._maximum:
            raise OpenRouterVideoCostPolicyError(
                "estimated OpenRouter video cost exceeds the configured limit",
                diagnostic_phase="cost_authorization",
                diagnostic_code="cost_limit_exceeded",
                diagnostic_metadata={
                    "pricing_sku": sku,
                    "estimated_cost_usd": str(estimated),
                    "max_estimated_cost_usd": str(self._maximum),
                },
            )
        return estimated, sku


class OpenRouterVideoPollingPolicy:
    def __init__(
        self,
        *,
        interval_seconds: float,
        max_seconds: float,
        max_attempts: int,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], Awaitable[None]],
        jitter: Callable[[int], float] = lambda _: 0.0,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.max_seconds = max_seconds
        self.max_attempts = max_attempts
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.jitter = jitter

    def delay(self, attempt: int, retry_after: float | None) -> float:
        base = retry_after if retry_after is not None else self.interval_seconds
        return max(0.001, min(base + self.jitter(attempt), self.interval_seconds * 4))

    @staticmethod
    def terminal(status: OpenRouterRemoteStatus) -> bool:
        return status in {
            OpenRouterRemoteStatus.COMPLETED,
            OpenRouterRemoteStatus.FAILED,
            OpenRouterRemoteStatus.CANCELLED,
            OpenRouterRemoteStatus.EXPIRED,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)
