"""Request-scoped logging and latency metrics.

Every user turn gets a request id. Filtering logs on that id reconstructs the
whole turn — what was asked, which tools ran with what arguments, how long
each took, and what was finally said. Without that, a slow or wrong answer in
production is unattributable: you can see that it was bad, not why.

Timings are also written as structured records so p95/p99 can be computed per
component and related back to the end-to-end number, plus the factors that
move it (input tokens, result counts).
"""

from __future__ import annotations

import contextvars
import json
import pathlib
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

#: Set per user turn, read by every log line and timing without threading it
#: through call signatures.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")

METRICS_PATH = pathlib.Path("/tmp/immigration-voice-metrics.jsonl")

#: Stages worth timing separately. Each maps to a real unit of work, so a
#: regression points at a component rather than "the agent got slower".
#: TTFT is the headline: for a voice agent it is what the user experiences as
#: responsiveness, because audio starts playing at the first token. Total
#: response time is perceived as answer *length*, not as lag.
STAGE_TTFT = "ttft"

#: Segments of the critical path to that first token. These are measured so
#: they sum to TTFT, which is what makes them useful for optimisation: a
#: component's share tells you where the time actually goes.
STAGE_TTFT_SEGMENT = "ttft_segment"

SEGMENT_TOOL_DECISION = "llm_tool_decision"   # user turn -> model emits tool call
SEGMENT_TOOL_EXEC = "tool_exec"               # the search itself
SEGMENT_ANSWER_FIRST_TOKEN = "llm_answer"     # tool result -> first answer token

#: Everything after the first token. Tracked separately so it never inflates
#: the responsiveness number.
STAGE_RESPONSE_TAIL = "response_tail"

STAGE_USER_TURN = "user_turn"          # end to end, transcript in -> answer out
STAGE_LLM_FIRST_TOKEN = "llm_first_token"
STAGE_LLM_TOTAL = "llm_total"
STAGE_TOOL = "tool"
STAGE_SEARCH_PROVIDER = "search_provider"


def new_request(session_id: str | None = None) -> str:
    """Start a new request scope and return its id."""
    request_id = uuid.uuid4().hex[:12]
    _request_id.set(request_id)
    if session_id:
        _session_id.set(session_id)
    return request_id


def current_request_id() -> str:
    return _request_id.get()


def bound() -> Any:
    """Logger bound to the current request, so every line carries the id."""
    return logger.bind(request_id=_request_id.get(), session_id=_session_id.get())


@dataclass
class Timing:
    stage: str
    name: str
    duration_ms: float
    request_id: str
    session_id: str
    started_at: float
    ok: bool = True
    #: Things that plausibly move latency, recorded so they can be correlated
    #: with it rather than guessed at — input size being the usual suspect.
    attributes: dict[str, Any] = field(default_factory=dict)


def record(timing: Timing) -> None:
    """Append one timing record. Best-effort: metrics must never break a call."""
    try:
        with METRICS_PATH.open("a") as handle:
            handle.write(json.dumps(asdict(timing)) + "\n")
    except OSError:
        pass


@contextmanager
def measure(stage: str, name: str, **attributes: Any):
    """Time a block, log it against the request, and record it for metrics.

    Yields a dict; anything put in it is merged into the recorded attributes,
    so a caller can add facts only known after the work (result counts, token
    usage) to the same record.
    """
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    ok = True
    try:
        yield extra
    except Exception:
        ok = False
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        merged = {**attributes, **extra}
        record(
            Timing(
                stage=stage,
                name=name,
                duration_ms=round(duration_ms, 2),
                request_id=_request_id.get(),
                session_id=_session_id.get(),
                started_at=started,
                ok=ok,
                attributes=merged,
            )
        )
        bound().info(
            f"stage={stage} name={name} ms={duration_ms:.0f} ok={ok} "
            + " ".join(f"{k}={v}" for k, v in merged.items())
        )


def log_user_query(text: str) -> None:
    bound().info(f"event=user_query text={text!r}")


def log_tool_call(name: str, arguments: dict) -> None:
    bound().info(f"event=tool_call name={name} args={json.dumps(arguments)[:400]}")


def log_tool_result(name: str, result: dict) -> None:
    # Summarised, not dumped: a full search payload buries the trace.
    summary = {
        key: (len(value) if isinstance(value, (list, dict)) else value)
        for key, value in (result or {}).items()
        if key in ("count", "results", "reports", "corroborated", "unavailable", "reason")
    }
    bound().info(f"event=tool_result name={name} summary={json.dumps(summary)}")


def log_agent_response(text: str) -> None:
    bound().info(f"event=agent_response chars={len(text)} text={text[:500]!r}")


def configure(level: str = "DEBUG") -> None:
    """Install a formatter that puts the request id on every line."""
    logger.remove()
    logger.add(
        lambda message: print(message, end=""),
        level=level,
        format=(
            "{time:HH:mm:ss.SSS} | {level: <7} | "
            "req={extra[request_id]} | {message}\n"
        ),
        # Lines logged outside a request scope still need the field present.
        filter=lambda record: record["extra"].setdefault("request_id", "-") is not None,
    )
