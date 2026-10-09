"""The agent, driven over text.

This is the only interface now. The browser converts speech to text (via its
own Web Speech API), then posts the text here; answers stream back as tokens
with status events mixed into the same stream.

Model-agnostic: it talks to whatever OpenAI-compatible model ``app.config``
names, with an automatic fallback model when the primary fails transiently.
The reasoning function can be routed to a dedicated reasoner model.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from app.config import settings
from app.failures import Failure, classify_exception
from app.llm import build_tools, get_client, stream_chat
from app.observability import (
    SEGMENT_ANSWER_FIRST_TOKEN,
    SEGMENT_TOOL_DECISION,
    SEGMENT_TOOL_EXEC,
    STAGE_TTFT,
    STAGE_TTFT_SEGMENT,
    Timing,
    bound,
    current_request_id,
    log_agent_response,
    log_tool_call,
    log_tool_result,
    record,
)
from app.prompts import SYSTEM_INSTRUCTION
from app.status import build_label
from app.tools.registry import _HANDLERS, _SCHEMAS
from app.tools.schema import ToolCallParams

#: A tool round is a search plus the model reading it. Four is generous for a
#: single question; past that the model is looping rather than converging.
MAX_TOOL_ROUNDS = 4

#: Restarts of a round whose stream died before any text reached the user.
#: Once words are on screen a retry would repeat them, so it stops instead.
MAX_STREAM_RETRIES = 1
STREAM_RETRY_BACKOFF_SECS = 0.5


class AgentUnavailable(Exception):
    """The turn could not be completed, with the reason already classified.

    Carries the Failure so the caller can tell the user whether this was a
    billing problem, a configuration problem, or a blip worth retrying —
    rather than every failure reaching them as "something went wrong".
    """

    def __init__(self, failure: Failure):
        super().__init__(f"{failure.kind.value}: {failure.detail}")
        self.failure = failure


@dataclass
class StatusEvent:
    """Something worth telling the user while they wait."""

    label: str
    round: int


#: Signals that a question asks for judgement — a comparison, a choice, a
#: likelihood — rather than a fact. Deliberately narrow: "can I travel while my
#: I-485 is pending" is a factual question that happens to start with "can I",
#: and sending it to an expensive reasoning model would be waste, not rigour.
_REASONING_SIGNALS = re.compile(
    r"\b("
    r"should i|which (?:is|one|route|path|option|visa)|"
    r"best (?:way|path|option|approach|strategy|route)|"
    r"fastest|quickest|"
    r"pros and cons|compare|compared|versus|\bvs\b|instead of|rather than|"
    r"chance|chances|likelihood|how likely|odds|probability|worth it|"
    r"strateg(?:y|ies)|my options|"
    r"what if|would it be|if i (?:were|had)|hypothetical"
    r")\b",
    re.IGNORECASE,
)


def looks_like_reasoning(question: str) -> bool:
    """True when the question is asking for judgement, not for a fact."""
    return bool(_REASONING_SIGNALS.search(question or ""))


def models_to_try(question: str = "") -> list[str]:
    """Ordered models for a turn: reasoner first for judgement questions.

    A dedicated reasoning model earns its extra cost on questions that need
    chain-of-thought — comparisons, choices, likelihoods — and is wasted on
    "how many days is the grace period". The route is a cheap regex rather
    than a classifier call, because a second model call to decide which model
    to use would cost more than it saves.
    """
    models: list[str] = []
    if settings.reasoner_model and looks_like_reasoning(question):
        models.append(settings.reasoner_model)
    models.append(settings.llm_model)
    if settings.llm_fallback_model and settings.llm_fallback_model not in models:
        models.append(settings.llm_fallback_model)
    return models


def build_messages(history: list[dict], question: str) -> list[dict]:
    """Turn a chat transcript into OpenAI-compatible messages.

    History arrives from the browser, which is the only place it is stored —
    nothing is persisted server-side.
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    for message in history:
        text = str(message.get("content", "")).strip()
        if not text:
            continue
        role = "assistant" if message.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": question})
    return messages


async def _execute_tool(
    name: str,
    arguments: dict,
    *,
    on_status: Callable[[StatusEvent | None], Awaitable[None]] | None,
    on_tool: Callable[[str, dict], Awaitable[None]] | None,
    round_index: int,
) -> dict:
    """Run one tool handler, returning its result for the model.

    A failing tool must not end the turn: the model is told what went wrong so
    it can say it could not check rather than quietly answering from memory.
    """
    log_tool_call(name, arguments)
    if on_tool is not None:
        await on_tool(name, arguments)
    if on_status is not None:
        await on_status(StatusEvent(label=build_label(name, arguments, round_number=round_index + 1), round=round_index + 1))

    params = ToolCallParams(arguments)
    handler = _HANDLERS.get(name)
    tool_start = time.perf_counter()
    if handler:
        try:
            await handler(params)
        except Exception as error:  # noqa: BLE001 - a tool must not end the turn
            failure = classify_exception(error)
            bound().warning(f"tool {name} failed ({failure.detail}, {failure.kind.value})")
            params.result = {
                "unavailable": True,
                "reason": failure.kind.value,
                "message": (
                    "This lookup failed, so you could not check a live source. "
                    "Tell the user plainly that you could not verify this right "
                    "now, and do not state any specific number, fee or deadline "
                    "from memory."
                ),
            }
    else:
        params.result = {"error": f"unknown tool {name}"}

    record(
        Timing(
            stage=STAGE_TTFT_SEGMENT,
            name=SEGMENT_TOOL_EXEC,
            duration_ms=round((time.perf_counter() - tool_start) * 1000, 2),
            request_id=current_request_id(),
            session_id="chat",
            started_at=tool_start,
            attributes={"tool": name, "round": round_index},
        )
    )
    result = params.result or {"error": "no handler"}
    log_tool_result(name, result)
    return result


def _to_openai_tool_calls(calls: list[dict], round_index: int) -> tuple[list[dict], list[tuple[str, str, dict]]]:
    """Convert accumulated calls into an OpenAI assistant tool_calls message.

    Returns the assistant message dict and a list of (call_id, name, arguments)
    for the caller to execute.
    """
    tool_calls = []
    callables = []
    for idx, call in enumerate(calls):
        call_id = call.get("id") or f"call_{round_index}_{idx}"
        name = call.get("name", "")
        arguments = call.get("arguments") or {}
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        )
        callables.append((call_id, name, arguments))
    return tool_calls, callables


async def stream_answer(
    history: list[dict],
    question: str,
    *,
    on_status: Callable[[StatusEvent | None], Awaitable[None]] | None = None,
    on_tool: Callable[[str, dict], Awaitable[None]] | None = None,
    usage_sink: dict | None = None,
) -> AsyncIterator[str]:
    """Answer one question, yielding text as it is produced.

    Tool calls happen between yields; those gaps are reported as status events
    so the UI can say what is happening instead of sitting still.
    """
    client = get_client()
    tools = build_tools(_SCHEMAS)
    messages = build_messages(history, question)

    turn_start = time.perf_counter()
    request_id = current_request_id()

    def emit(stage: str, name: str, ms: float, started: float, **attributes):
        record(
            Timing(
                stage=stage,
                name=name,
                duration_ms=round(ms, 2),
                request_id=request_id,
                session_id="chat",
                started_at=started,
                attributes=attributes,
            )
        )

    async def status(event: StatusEvent | None) -> None:
        if on_status is not None:
            await on_status(event)

    #: Rounds are separate model calls, so text from one runs straight into
    #: text from the next. A paragraph break is inserted between rounds.
    text_already_yielded = False

    for round_index in range(MAX_TOOL_ROUNDS):
        segment_start = time.perf_counter()
        first_token_ms: float | None = None
        round_text: list[str] = []
        tool_calls: list[dict] = []
        round_opened = False

        # Chosen once per round, not per attempt: the route depends on the
        # question, and re-deciding it mid-round could switch models between
        # the tool call and the answer that reads the tool result.
        models = models_to_try(question)
        for model_index, model in enumerate(models):
            is_last_model = model_index == len(models) - 1
            attempts = 0
            while True:
                yielded_this_round = 0
                round_text = []
                tool_calls = []
                first_token_ms = None
                try:
                    async for delta in stream_chat(
                        client, model=model, messages=messages, tools=tools
                    ):
                        if first_token_ms is None and (delta.content or delta.tool_calls):
                            first_token_ms = (time.perf_counter() - segment_start) * 1000
                        if delta.content:
                            if text_already_yielded and not round_opened:
                                yield "\n\n"
                            round_opened = True
                            text_already_yielded = True
                            yielded_this_round += len(delta.content)
                            round_text.append(delta.content)
                            yield delta.content
                        if delta.tool_calls:
                            tool_calls = delta.tool_calls
                        # Token usage arrives on the final chunk of each round,
                        # so rounds are summed into the caller's sink. The
                        # eval harness uses this to report cost per case.
                        if delta.usage and usage_sink is not None:
                            for key, value in delta.usage.items():
                                usage_sink[key] = usage_sink.get(key, 0) + value
                    # Round completed without raising.
                    break
                except Exception as error:  # noqa: BLE001 - classified and re-raised
                    failure = classify_exception(error)
                    can_retry = (
                        failure.retryable
                        and yielded_this_round == 0
                        and attempts < MAX_STREAM_RETRIES
                    )
                    if can_retry:
                        attempts += 1
                        bound().info(f"stream failed ({failure.detail}); retrying {model}")
                        await asyncio.sleep(STREAM_RETRY_BACKOFF_SECS)
                        continue
                    # Try the next model if this one is spent and nothing was
                    # shown yet; otherwise surface the failure.
                    if failure.retryable and yielded_this_round == 0 and not is_last_model:
                        bound().warning(f"{model} failed ({failure.detail}); falling back")
                        break
                    bound().warning(f"turn failed ({failure.detail}, {failure.kind.value})")
                    raise AgentUnavailable(failure) from error

        if tool_calls:
            emit(
                STAGE_TTFT_SEGMENT,
                SEGMENT_TOOL_DECISION,
                first_token_ms if first_token_ms is not None else 0.0,
                segment_start,
                round=round_index,
            )
            assistant_calls, callables = _to_openai_tool_calls(tool_calls, round_index)
            messages.append(
                {"role": "assistant", "content": "".join(round_text) or None, "tool_calls": assistant_calls}
            )

            tool_results = []
            for call_id, name, arguments in callables:
                result = await _execute_tool(
                    name, arguments, on_status=status, on_tool=on_tool, round_index=round_index
                )
                tool_results.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)})
            messages.extend(tool_results)
            continue

        # No tool call: this round produced the answer.
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

    # Ran out of tool rounds. Saying so is better than returning nothing.
    bound().warning(f"hit MAX_TOOL_ROUNDS ({MAX_TOOL_ROUNDS}) without an answer")
    await status(None)
    yield (
        "I wasn't able to pin that down from my sources just now. "
        "Could you narrow the question a little, or try again in a moment?"
    )
