"""Tiny retry helper for transient external-API failures (STT/TTS/LLM).

Backs off briefly between attempts. Returns a fallback value (or last
exception value) on final failure rather than raising — voice pipelines
must never crash on a single bad upstream response.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Tuple, Type

logger = logging.getLogger(__name__)


async def retry_async(
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 2,
    backoff_seconds: float = 0.05,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    fallback: Any = None,
    label: str = "",
) -> Any:
    """Run `fn()` up to `attempts` times. Return its result, or `fallback`
    on exhaustion. Logs each retry at INFO level."""
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            result = await fn()
            # Allow caller to detect "soft" failures by returning a falsy
            # value. We only retry on exceptions; falsy results pass through.
            return result
        except retry_on as e:
            last_exc = e
            if i < attempts:
                logger.info(
                    "retry %s attempt %d/%d failed: %s",
                    label or fn.__name__,
                    i,
                    attempts,
                    e,
                )
                await asyncio.sleep(backoff_seconds * i)
    logger.warning(
        "retry %s exhausted after %d attempts: %s",
        label or fn.__name__,
        attempts,
        last_exc,
    )
    return fallback
