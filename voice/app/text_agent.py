"""The agent, driven over text instead of speech.

The voice path cannot serve this. `gemini-live-2.5-flash-native-audio` refuses
a TEXT response modality outright — "Text output is not supported for native
audio output model" — so chat runs the text-serving sibling of the same
family. That divergence is worth stating plainly: the two interfaces run
different models and can answer the same question differently.

It is the chat path, though, that carries the evidence. The eval suite scores
this model and these tools; the native-audio model in voice is not what those
49 cases measure.

Everything else is deliberately shared with voice — the same composed system
prompt, the same tool schemas, the same handlers really hitting the same
providers. A chat answer and a voice answer should differ in delivery, not in
what the agent knows or refuses to do.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Awaitable
from dataclasses import dataclass

from google import genai
from google.genai import types

from app.observability import (
    SEGMENT_ANSWER_FIRST_TOKEN,
    SEGMENT_TOOL_DECISION,
    SEGMENT_TOOL_EXEC,
    STAGE_TTFT,
    STAGE_TTFT_SEGMENT,
    Timing,
    bound,
    current_request_id,
    log_tool_call,
    log_tool_result,
    record,
)
from app.prompts import SYSTEM_INSTRUCTION
from app.status import build_label
from app.tools.registry import _HANDLERS, _SCHEMAS

#: The text-serving model of the same family as the voice model. Named here
#: rather than in config because it is not interchangeable with the voice
#: model: one of them cannot emit text and the other cannot emit audio.
CHAT_MODEL = "gemini-2.5-flash"

#: A tool round is a search plus the model reading it. Four is generous for a
#: single question; past that the model is looping rather than converging.
MAX_TOOL_ROUNDS = 4


@dataclass
class StatusEvent:
    """Something worth telling the user while they wait."""

    label: str
    round: int


class _Params:
    """Stands in for pipecat's FunctionCallParams outside a live pipeline.

    The tool handlers are written against pipecat's calling convention so that
    voice and chat run the identical code; this adapts that convention to a
    plain request/response call.
    """

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None

    async def result_callback(self, value):
        self.result = value


def tool_declarations() -> list[types.Tool]:
    """Mirror the production tool schemas into genai declarations."""
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=schema.name,
                    description=schema.description,
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            key: types.Schema(
                                type=(value.get("type", "string")).upper(),
                                description=value.get("description", ""),
                            )
                            for key, value in (schema.properties or {}).items()
                        },
                        required=list(schema.required or []),
                    ),
                )
                for schema in _SCHEMAS
            ]
        )
    ]


def _has_content(chunk) -> bool:
    """True once a chunk carries real output — text or a function call."""
    for candidate in getattr(chunk, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None) or getattr(part, "function_call", None):
                return True
    return False


def build_contents(history: list[dict], question: str) -> list[types.Content]:
    """Turn a chat transcript into model contents.

    History arrives from the browser, which is the only place it is stored —
    nothing is persisted server-side, so the client is the source of truth for
    what was said earlier in the session.
    """
    contents: list[types.Content] = []
    for message in history:
        text = str(message.get("content", "")).strip()
        if not text:
            continue
        role = "model" if message.get("role") == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    return contents


async def stream_answer(
    client: genai.Client,
    history: list[dict],
    question: str,
    *,
    on_status: Callable[[StatusEvent | None], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """Answer one question, yielding text as it is produced.

    Tool calls happen between yields, which is exactly the gap that voice
    covers with filler audio. Here the same moment is reported as a status
    event, so the chat UI can say what is happening instead of sitting still.
    """
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tool_declarations(),
        temperature=0,
        # Handlers are dispatched here so they run the production code path
        # and can report progress, rather than being resolved inside the SDK.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = build_contents(history, question)

    turn_start = time.perf_counter()
    request_id = current_request_id()
    session_id = "chat"

    def emit(stage: str, name: str, ms: float, started: float, **attributes):
        record(
            Timing(
                stage=stage,
                name=name,
                duration_ms=round(ms, 2),
                request_id=request_id,
                session_id=session_id,
                started_at=started,
                attributes=attributes,
            )
        )

    async def status(event: StatusEvent | None) -> None:
        if on_status is not None:
            await on_status(event)

    #: Rounds are separate model calls, so text from one runs straight into
    #: text from the next: "let me check that.The grace period is...". In
    #: voice the pause between rounds supplies the break; in text nothing
    #: does, so it has to be inserted.
    text_already_yielded = False

    for round_index in range(MAX_TOOL_ROUNDS):
        segment_start = time.perf_counter()
        first_token_ms: float | None = None
        parts: list = []
        round_opened = False

        stream = await client.aio.models.generate_content_stream(
            model=CHAT_MODEL, contents=contents, config=config
        )
        async for chunk in stream:
            if first_token_ms is None and _has_content(chunk):
                first_token_ms = (time.perf_counter() - segment_start) * 1000
            for candidate in getattr(chunk, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    parts.append(part)
                    # Stream text out as it arrives rather than after the
                    # round completes: the whole point of the text path is
                    # that the user sees words appearing.
                    if getattr(part, "text", None):
                        if text_already_yielded and not round_opened:
                            yield "\n\n"
                        round_opened = True
                        text_already_yielded = True
                        yield part.text

        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not calls:
            if first_token_ms is not None:
                emit(
                    STAGE_TTFT_SEGMENT,
                    SEGMENT_ANSWER_FIRST_TOKEN,
                    first_token_ms,
                    segment_start,
                    round=round_index,
                )
                emit(
                    STAGE_TTFT,
                    "answer",
                    (segment_start - turn_start) * 1000 + first_token_ms,
                    turn_start,
                    tool_calls=round_index,
                )
            await status(None)
            return

        emit(
            STAGE_TTFT_SEGMENT,
            SEGMENT_TOOL_DECISION,
            first_token_ms if first_token_ms is not None else 0.0,
            segment_start,
            round=round_index,
        )
        contents.append(types.Content(role="model", parts=parts))

        reply_parts = []
        for call in calls:
            arguments = dict(call.args or {})
            log_tool_call(call.name, arguments)
            await status(
                StatusEvent(
                    label=build_label(call.name, arguments, round_number=round_index + 1),
                    round=round_index + 1,
                )
            )

            params = _Params(arguments)
            handler = _HANDLERS.get(call.name)
            tool_start = time.perf_counter()
            if handler:
                await handler(params)
            emit(
                STAGE_TTFT_SEGMENT,
                SEGMENT_TOOL_EXEC,
                (time.perf_counter() - tool_start) * 1000,
                tool_start,
                tool=call.name,
                round=round_index,
            )
            log_tool_result(call.name, params.result or {})
            reply_parts.append(
                types.Part.from_function_response(
                    name=call.name, response=params.result or {"error": "no handler"}
                )
            )
        contents.append(types.Content(role="user", parts=reply_parts))

    # Ran out of tool rounds. Saying so is better than returning nothing —
    # the user has been watching status updates and is owed an explanation.
    bound().warning(f"hit MAX_TOOL_ROUNDS ({MAX_TOOL_ROUNDS}) without an answer")
    await status(None)
    yield (
        "I wasn't able to pin that down from my sources just now. "
        "Could you narrow the question a little, or try again in a moment?"
    )
