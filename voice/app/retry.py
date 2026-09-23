"""Bounded retries for outbound calls.

There were three separate retry implementations before this: one in the eval
harness, one inside the search providers, and none at all around the model
call — which is how a dropped stream ended up scored as an unsafe answer.
They disagreed about what was worth retrying, and the model path had the
weakest version of the answer.

Two rules hold everywhere here:

* Retry only what a retry can fix. Auth and billing failures return the same
  answer every time, so retrying them buys nothing and costs the user a
  second of silence in a conversation that is already waiting.
* Bound retries by a deadline, not a count. A count lets a retry start at
  4.5s of a 6s budget and make the turn worse than the failure would have.
  With a deadline, a retry only happens when there is time for it to help.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from loguru import logger

from app.failures import Failure, classify_exception

T = TypeVar("T")

#: One retry by default. A second rarely converts a failure into a success
#: and always doubles the wait a user sits through.
DEFAULT_MAX_RETRIES = 1

#: Long enough to clear a blip, short enough to stay inside a voice turn.
DEFAULT_BACKOFF_SECS = 0.25


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    what: str,
    deadline: float | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_secs: float = DEFAULT_BACKOFF_SECS,
) -> T:
    """Run an awaitable, retrying only transient failures within budget.

    `deadline` is a `time.monotonic()` value. Without one, only the retry
    count bounds the work — use that for background jobs, never for anything
    a user is waiting through.

    Raises the last exception if every attempt fails, so the caller still
    decides what the user is told.
    """
    attempt = 0
    while True:
        try:
            return await operation()
        except Exception as error:  # noqa: BLE001 - classified, then re-raised
            failure: Failure = classify_exception(error)
            out_of_attempts = attempt >= max_retries
            out_of_time = deadline is not None and (deadline - time.monotonic()) <= backoff_secs

            if not failure.retryable or out_of_attempts or out_of_time:
                reason = (
                    "not retryable"
                    if not failure.retryable
                    else "out of attempts"
                    if out_of_attempts
                    else "out of budget"
                )
                logger.warning(
                    f"{what} failed ({failure.detail or type(error).__name__}, "
                    f"{failure.kind.value}); giving up — {reason}"
                )
                raise

            attempt += 1
            logger.info(
                f"{what} failed ({failure.detail or type(error).__name__}); "
                f"retry {attempt}/{max_retries} in {backoff_secs:.2f}s"
            )
            await asyncio.sleep(backoff_secs)
