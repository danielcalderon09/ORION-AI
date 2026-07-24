"""Bounded, cancellable subprocess I/O shared by local media adapters."""

import asyncio


class SubprocessOutputLimitError(RuntimeError):
    """Raised when a controlled media process exceeds its diagnostic budget."""


async def communicate_limited(
    process: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("media subprocess pipes are unavailable")
    readers = (
        asyncio.create_task(_read_limited(process.stdout, stdout_limit)),
        asyncio.create_task(_read_limited(process.stderr, stderr_limit)),
    )
    waiter = asyncio.create_task(process.wait())
    tasks = (*readers, waiter)
    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=timeout_seconds,
        )
        return stdout, stderr
    except (TimeoutError, SubprocessOutputLimitError, asyncio.CancelledError):
        await terminate_process(process)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _read_limited(
    stream: asyncio.StreamReader,
    maximum: int,
) -> bytes:
    content = bytearray()
    while True:
        chunk = await stream.read(min(65_536, maximum + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > maximum:
            raise SubprocessOutputLimitError(
                "media subprocess output exceeded its safe limit"
            )
