"""Telling the client what the agent is doing, without telling it how.

The chat UI shows a status line while a turn is in flight, the way a person
says "hang on, looking that up" instead of going quiet. The voice client uses
the same events to fade in a soft working tone, which has to happen on the
client: audio frames play in queue order, so a tone pushed from the pipeline
would sit in front of the answer rather than under it.

Labels are written for the person waiting. They name the kind of work, never
the tool, the provider, or the query — those are our implementation details,
and a status line that leaks them reads as debug output.
"""

from __future__ import annotations

from loguru import logger

#: What each tool looks like from outside. Anything unmapped falls back to
#: the generic label rather than exposing a function name.
TOOL_LABELS = {
    "search_official_guidance": "Checking official guidance",
    "search_community_experiences": "Looking for people's experiences",
}

DEFAULT_LABEL = "Working on it"


class AgentStatus:
    """Pushes agent-status events to the client over the RTVI channel."""

    def __init__(self) -> None:
        #: Bound after construction: tool handlers are registered on the LLM
        #: service, which is built before the RTVI processor exists.
        self._rtvi = None
        self._active = 0

    def bind(self, rtvi) -> None:
        self._rtvi = rtvi

    async def working(self, function_name: str) -> None:
        self._active += 1
        await self._send(
            {
                "type": "agent-status",
                "state": "working",
                "label": TOOL_LABELS.get(function_name, DEFAULT_LABEL),
            }
        )

    async def done(self) -> None:
        """Clear the status once nothing is outstanding.

        Reference-counted: two tools can run at once, and the first to finish
        must not clear a status the second still needs.
        """
        self._active = max(0, self._active - 1)
        if self._active == 0:
            await self._send({"type": "agent-status", "state": "idle"})

    async def _send(self, message: dict) -> None:
        if self._rtvi is None:
            return
        try:
            await self._rtvi.send_server_message(message)
        except Exception as error:  # noqa: BLE001 - status is never load-bearing
            logger.warning(f"could not send status to client: {error!r}")
