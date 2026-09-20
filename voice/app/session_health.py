"""Turns a dead model into a spoken-to user rather than silence.

When Gemini fails, nothing downstream can generate speech — so the user just
sees the orb sitting there. This watches for error frames and pushes a plain
message to the browser so the UI can say what happened.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import ErrorFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frameworks.rtvi import RTVIProcessor

from app.failures import FailureKind, classify_error_frame, session_failure_message


class SessionFailureObserver(BaseObserver):
    """Report model-level failures to the client as a user-facing message."""

    def __init__(self, rtvi: RTVIProcessor):
        super().__init__()
        self._rtvi = rtvi
        self._reported = False

    async def on_push_frame(self, data: FramePushed) -> None:
        if not isinstance(data.frame, ErrorFrame):
            return
        # One session produces a burst of related errors; the user needs to
        # be told once, not once per frame.
        if self._reported:
            return
        self._reported = True

        kind = classify_error_frame(data.frame)
        message = session_failure_message(kind)
        logger.error(
            f"session failure ({kind.value}): {getattr(data.frame, 'error', '')!r} "
            f"-> telling client"
        )

        try:
            await self._rtvi.send_server_message(
                {
                    "type": "session-failure",
                    "kind": kind.value,
                    "message": message,
                    # Funds and auth problems will not fix themselves on a
                    # retry, so the UI should not invite one.
                    "retryable": kind is FailureKind.CONNECTIVITY,
                }
            )
        except Exception:
            logger.exception("could not deliver the failure message to the client")
