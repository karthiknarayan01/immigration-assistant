"""Model routing over any OpenAI-compatible endpoint.

The agent used to be hard-wired to Gemini on Vertex, which locked the model
choice. This module talks to any OpenAI-compatible API (OpenRouter by default,
or Grok/Together/DeepSeek/Groq directly) so the model is pure configuration:

    LLM_MODEL=deepseek/deepseek-chat
    LLM_FALLBACK_MODEL=meta-llama/llama-3.3-70b-instruct
    LLM_BASE_URL=https://openrouter.ai/api/v1
    LLM_API_KEY=sk-or-...

The only responsibility here is transport: open a client, turn our tool schema
into the provider's wire format, and stream one round of chat — accumulating
both text and tool calls from the stream. Policy (which model, what fallback,
what temperature) lives in the caller.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from loguru import logger
from openai import AsyncOpenAI

from app.config import settings
from app.tools.schema import FunctionSchema

_client: AsyncOpenAI | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_client() -> AsyncOpenAI:
    """One shared client per event loop, like the search providers' client.

    The OpenAI SDK pools HTTP connections internally; creating a client per
    round paid fresh TLS handshakes on every model call. A client is bound to
    the loop that opened it, so it is rebuilt when the loop changes (tests,
    eval harness).
    """
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _client is None or _client_loop is not loop:
        _client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            # Some local endpoints (Ollama, vLLM) need no key but still reject
            # a missing Authorization header, so a placeholder is supplied.
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.model_timeout_secs,
            max_retries=0,  # retries live in app/retry.py, where the budget is known
        )
        _client_loop = loop
    return _client


def build_tools(tools: list[FunctionSchema]) -> list[dict]:
    """Convert our provider-agnostic schema into OpenAI tool-calling JSON."""
    declarations = []
    for tool in tools:
        properties = {}
        for name, spec in (tool.properties or {}).items():
            # spec values follow OpenAI's JSON-schema-ish shape directly.
            properties[name] = spec
        declarations.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(tool.required or []),
                    },
                },
            }
        )
    return declarations


@dataclass
class ChatDelta:
    """One piece of a streaming round.

    ``content`` is the text produced since the previous delta. ``tool_calls``
    is the *complete* set accumulated so far (each argument already parsed to a
    dict), so by stream end the caller has every tool call the model issued.
    """

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)


async def stream_chat(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0,
    tool_choice: str | None = None,
) -> AsyncIterator[ChatDelta]:
    """Stream one chat completion, yielding text and accumulated tool calls.

    Raises on failure; the caller decides retry/fallback and what the user is
    told. Tool calls arrive as deltas across chunks and are merged here so the
    caller sees whole, parsed calls rather than fragments.

    `tool_choice="required"` makes a lookup mandatory rather than optional.
    A system prompt cannot achieve that reliably: measured on two models, tool
    use for ordinary factual questions fell from 3/3 with no system prompt to
    1-2/3 with one, and rewording the rule did not restore it. When the
    product's promise is a grounded answer, whether it looked anything up
    cannot be left to the model's mood.
    """
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools or None,
        temperature=temperature,
        stream=True,
        stream_options={"include_usage": True},
        **({"tool_choice": tool_choice} if tool_choice and tools else {}),
    )

    calls: dict[int, dict] = {}
    usage: dict = {}
    finish_reason: str | None = None

    async for chunk in response:
        if getattr(chunk, "usage", None):
            u = chunk.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
            }

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue

        if choices[0].finish_reason:
            finish_reason = choices[0].finish_reason

        content = getattr(delta, "content", None)
        if content:
            yield ChatDelta(content=content, usage=usage)

        for tc in getattr(delta, "tool_calls", None) or []:
            idx = tc.index
            entry = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if getattr(tc, "id", None):
                entry["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    entry["name"] += fn.name
                if getattr(fn, "arguments", None):
                    entry["arguments"] += fn.arguments

    parsed = []
    for entry in calls.values():
        try:
            args = json.loads(entry["arguments"] or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        if entry["name"]:
            parsed.append(
                {"id": entry["id"], "name": entry["name"], "arguments": args}
            )

    if parsed:
        yield ChatDelta(tool_calls=parsed, finish_reason=finish_reason, usage=usage)
    elif finish_reason:
        yield ChatDelta(finish_reason=finish_reason, usage=usage)
