"""FFprobe-only adapter for independent final validation."""

from backend.src.production.render_validation.exceptions import FinalRenderValidationError
from backend.src.production.render_validation.ports import VerifiedFinalRenderSource
from backend.src.production.rendering.output_probe import (
    ProbedRenderOutput,
    probe_render_output,
)
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner


class FFprobeFinalRenderProbe:
    def __init__(self, *, runner: ControlledMediaProcessRunner) -> None:
        self._runner = runner
        self.invocation_count = 0

    async def probe(self, source: VerifiedFinalRenderSource) -> ProbedRenderOutput:
        self.invocation_count += 1
        return await probe_render_output(
            runner=self._runner,
            path=source.render_path,
            request=source.request,
            plan=source.execution_plan,
        )


class DisabledFinalRenderProbe:
    """Fail closed when no FFprobe-capable renderer is configured."""

    async def probe(self, source: VerifiedFinalRenderSource) -> ProbedRenderOutput:
        del source
        raise FinalRenderValidationError(
            "ffprobe_unavailable",
            "final render probing is unavailable",
        )
