"""Provider-agnostic tool schema and call adapter.

The agent used to declare tools through pipecat's schema types and run handlers
through pipecat's ``FunctionCallParams``. That tied the tool layer to the voice
pipeline. These stand-ins keep the same shape so the handlers themselves barely
change, while the transport (text chat now) can build whichever wire format the
model provider wants — OpenAI-compatible tool JSON today.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionSchema:
    """One tool the model may call. ``properties`` maps name -> JSON Schema."""

    name: str
    description: str
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)


@dataclass
class ToolsSchema:
    standard_tools: list[FunctionSchema] = field(default_factory=list)


class ToolCallParams:
    """The calling convention tool handlers are written against.

    Mirrors the two things a handler needs: the model-supplied ``arguments``
    and a ``result_callback`` that hands the result back to the agent loop.
    """

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None

    async def result_callback(self, value):
        self.result = value
