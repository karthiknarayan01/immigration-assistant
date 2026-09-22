"""Run the eval sets against the agent and score them with a second model.

Two things keep the number honest:

* The agent runs with the production system prompt and the production tools,
  which really execute and really hit the search providers.
* Cases are split into `tune` and `holdout` by a stable hash. Prompt changes
  may only be made against `tune`. The headline number is `holdout`, which
  the tuning never sees — otherwise the score just measures how well the
  prompt was fitted to the questions.

Usage:
    PYTHONPATH=. uv run python evals/run_eval.py [--limit N] [--split tune|holdout|all]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx
import yaml
from google import genai
from google.genai import types

from app.config import settings
from app.observability import (
    SEGMENT_ANSWER_FIRST_TOKEN,
    SEGMENT_TOOL_DECISION,
    SEGMENT_TOOL_EXEC,
    STAGE_RESPONSE_TAIL,
    STAGE_TTFT,
    STAGE_TTFT_SEGMENT,
    STAGE_USER_TURN,
    Timing,
    current_request_id,
    measure,
    new_request,
    record,
)
from app.prompts import SYSTEM_INSTRUCTION
from app.tools.registry import _HANDLERS, _SCHEMAS
from evals.score import FACTORS, JUDGE_MODEL, judge

HERE = pathlib.Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
RESULTS = HERE / "results"

#: The agent in production is a native-audio Live model. Driving that model
#: through a full tool round-trip from a script proved unreliable — it issues
#: the call, speaks a filler, then ends the turn without consuming the result
#: — so the eval runs the same system prompt and the same tools through the
#: text API. This measures answer substance, tool use, hedging and safety.
#: It does not measure voice, turn-taking or latency.
EVAL_MODEL = "gemini-2.5-flash"

MAX_TOOL_ROUNDS = 4

#: Vertex rate-limits under sustained use, and a run that dies at case 30 is
#: worth nothing. Retry 429s with backoff and pace requests between cases.
RATE_LIMIT_RETRIES = 5
INTER_CASE_DELAY_SECS = 2.0


#: Transport failures, not application errors. Vertex drops long-lived
#: connections under sustained use: four separate full runs died on
#: "Server disconnected without sending a response" between cases 34 and 44,
#: which is a property of the connection rather than of the case being judged.
#: Treating these as fatal threw away every completed case in the run.
_RETRYABLE_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
)

#: Retryable when raised as a plain API error rather than a typed exception.
_RETRYABLE_MARKERS = ("RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE", "INTERNAL", "500")


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, _RETRYABLE_ERRORS):
        return True
    text = str(error)
    return any(marker in text for marker in _RETRYABLE_MARKERS)


async def with_backoff(operation, *, what: str):
    """Retry an API call through rate limiting and dropped connections."""
    delay = 4.0
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return await operation()
        except Exception as error:  # noqa: BLE001 - re-raised unless transient
            if not _is_retryable(error):
                raise
            if attempt == RATE_LIMIT_RETRIES - 1:
                raise
            print(
                f"    {type(error).__name__} on {what}; retrying in {delay:.0f}s",
                flush=True,
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")

def _report(rows: list[dict], unjudged: list[dict], args) -> dict:
    """Build the report from whatever has been scored so far.

    Called after every case as well as at the end, so an interrupted run still
    leaves a readable partial result. `cases_unjudged` is reported alongside
    the scores rather than folded into them — a partial run has to be visibly
    partial, or it gets quoted as if it were a complete one.
    """
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eval_model": EVAL_MODEL,
        "tools_enabled": not args.no_tools,
        "production_model": settings.gemini_model,
        "judge_model": JUDGE_MODEL,
        "cases_unjudged": len(unjudged),
        "unjudged": unjudged,
        "overall": summarise(rows),
        "tune": summarise([r for r in rows if r["split"] == "tune"]),
        "holdout": summarise([r for r in rows if r["split"] == "holdout"]),
    }


#: Roughly half, assigned deterministically so the split cannot drift between
#: runs — and so nobody can quietly move a failing case into `tune`.
HOLDOUT_FRACTION = 0.5


def split_for(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < HOLDOUT_FRACTION else "tune"


def load_cases() -> list[dict]:
    """Load every task's cases, tagging each with the task that owns it.

    One folder per task, mirroring app/prompts/: a task's score is then
    attributable to its own prompt file rather than to "the agent".
    """
    cases: list[dict] = []
    for task_dir in sorted(TASKS_DIR.iterdir()):
        path = task_dir / "cases.yaml"
        if not task_dir.is_dir() or not path.exists():
            continue
        for case in yaml.safe_load(path.read_text()) or []:
            case["task"] = task_dir.name
            case.setdefault("source_set", "handwritten")
            case["split"] = split_for(case["id"])
            cases.append(case)
    return cases


def _tool_declarations() -> list[types.Tool]:
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
                                description=value.get("description"),
                            )
                            for key, value in schema.properties.items()
                        },
                        required=schema.required,
                    ),
                )
                for schema in _SCHEMAS
            ]
        )
    ]


def _has_content(chunk) -> bool:
    """True once a chunk carries real output — text or a function call."""
    for candidate in (getattr(chunk, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "text", None) or getattr(part, "function_call", None):
                return True
    return False


class _Params:
    """Stands in for pipecat's FunctionCallParams outside a live pipeline."""

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None

    async def result_callback(self, value):
        self.result = value


async def ask(client: genai.Client, question: str) -> tuple[str, list[str]]:
    """Put one question to the agent, running any tools it calls for real.

    Instrumented around the critical path to the first spoken token, because
    that is what a voice user experiences as responsiveness. The segments are
    measured so they sum to TTFT: each one's share says where optimisation
    effort would actually pay.
    """
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=_tool_declarations(),
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=question)])
    ]
    tools_used: list[str] = []

    turn_start = time.perf_counter()
    request_id = current_request_id()

    def emit(stage: str, name: str, ms: float, started: float, **attributes):
        record(
            Timing(
                stage=stage,
                name=name,
                duration_ms=round(ms, 2),
                request_id=request_id,
                session_id="eval",
                started_at=started,
                attributes=attributes,
            )
        )

    for round_index in range(MAX_TOOL_ROUNDS):
        approx_input_tokens = sum(
            len(getattr(part, "text", "") or "") for c in contents for part in (c.parts or [])
        ) // 4

        segment_start = time.perf_counter()
        first_token_ms: float | None = None
        chunks = []
        usage = None

        stream = await with_backoff(
            lambda: client.aio.models.generate_content_stream(
                model=EVAL_MODEL, contents=contents, config=config
            ),
            what="agent",
        )
        # Parts are collected as they stream. Rebuilding a response object
        # from chunks silently dropped them, which showed up as the agent
        # "saying nothing" on five cases — a harness bug that reads exactly
        # like a model failure in the scores.
        async for chunk in stream:
            if first_token_ms is None and _has_content(chunk):
                first_token_ms = (time.perf_counter() - segment_start) * 1000
            for cand in (getattr(chunk, "candidates", None) or []):
                content = getattr(cand, "content", None)
                chunks.extend(getattr(content, "parts", None) or [])
            if getattr(chunk, "usage_metadata", None):
                usage = chunk.usage_metadata

        total_ms = (time.perf_counter() - segment_start) * 1000
        tokens = {}
        if usage is not None:
            tokens["prompt_tokens"] = getattr(usage, "prompt_token_count", 0) or 0
            tokens["output_tokens"] = getattr(usage, "candidates_token_count", 0) or 0

        parts = chunks
        calls = [part.function_call for part in parts if getattr(part, "function_call", None)]

        if calls:
            # This model call ends at the tool-call decision; the user is still
            # waiting, so its first-token time is a TTFT segment.
            emit(
                STAGE_TTFT_SEGMENT,
                SEGMENT_TOOL_DECISION,
                first_token_ms if first_token_ms is not None else total_ms,
                segment_start,
                round=round_index,
                approx_input_tokens=approx_input_tokens,
                **tokens,
            )
            contents.append(types.Content(role="model", parts=parts))

            reply_parts = []
            for call in calls:
                tools_used.append(call.name)
                handler = _HANDLERS.get(call.name)
                params = _Params(dict(call.args or {}))
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
                reply_parts.append(
                    types.Part.from_function_response(
                        name=call.name, response=params.result or {"error": "no handler"}
                    )
                )
            contents.append(types.Content(role="user", parts=reply_parts))
            continue

        # No tool call: this round produced the answer the user hears.
        if first_token_ms is not None:
            emit(
                STAGE_TTFT_SEGMENT,
                SEGMENT_ANSWER_FIRST_TOKEN,
                first_token_ms,
                segment_start,
                round=round_index,
                approx_input_tokens=approx_input_tokens,
                **tokens,
            )
            ttft_ms = (segment_start - turn_start) * 1000 + first_token_ms
            emit(
                STAGE_TTFT,
                "answer",
                ttft_ms,
                turn_start,
                tool_calls=len(tools_used),
                **tokens,
            )
            # Everything after the first token is answer length, not lag.
            emit(
                STAGE_RESPONSE_TAIL,
                "stream",
                total_ms - first_token_ms,
                segment_start,
                **tokens,
            )

        text = "".join(part.text for part in parts if getattr(part, "text", None))
        return text.strip(), tools_used

    return "", tools_used


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    means = [row["mean"] for row in rows]

    by_factor = {
        factor: round(statistics.mean(row["scores"][factor] for row in rows), 2)
        for factor in FACTORS
    }
    by_category: dict[str, list[float]] = defaultdict(list)
    by_task: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row["mean"])
        by_task[row.get("task", "?")].append(row["mean"])

    return {
        "cases": len(rows),
        "mean": round(statistics.mean(means), 2),
        "pass_rate_pct": round(100 * sum(row["passed"] for row in rows) / len(rows)),
        "unsafe_pct": round(100 * sum(row["unsafe"] for row in rows) / len(rows)),
        "by_factor": by_factor,
        "by_category": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_category.items())
        },
        "by_task": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_task.items())
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=("tune", "holdout", "all"), default="all")
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help=(
            "Run with search disabled, as if the provider credits were "
            "exhausted. Measures degraded-mode behaviour: the agent should "
            "say it could not check rather than guessing at current policy."
        ),
    )
    args = parser.parse_args()

    if args.no_tools:
        # Emptying the keys is what the tools themselves check, so this
        # exercises the real degraded path rather than a special test mode.
        settings.tavily_api_key = ""
        settings.exa_api_key = ""
        settings.parallel_api_key = ""
        print("running with search DISABLED (simulating exhausted credits)\n")

    cases = load_cases()
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]

    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project_id,
        location=settings.google_cloud_location,
    )

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS / f"{stamp}.json"

    rows = []
    #: Cases whose judge call could not be completed. Deliberately kept out of
    #: `rows` rather than scored as zeros: an unjudged case is missing data,
    #: and averaging it in as zero would read as a quality regression.
    unjudged = []
    for index, case in enumerate(cases, start=1):
        new_request(session_id="eval")
        try:
            with measure(STAGE_USER_TURN, "turn", case_id=case["id"]):
                answer, tools_used = await ask(client, case["question"])
        except Exception as error:  # noqa: BLE001 - one bad case must not end the run
            answer, tools_used = "", [f"ERROR: {type(error).__name__}"]

        try:
            verdict = await with_backoff(
                lambda: asyncio.to_thread(judge, client, case, answer), what="judge"
            )
        except Exception as error:  # noqa: BLE001 - a dead judge must not end the run
            unjudged.append({"id": case["id"], "error": f"{type(error).__name__}: {error}"})
            print(f"[{index:>2}/{len(cases)}] {case['id']:<8} JUDGE FAILED ({type(error).__name__})",
                  flush=True)
            await asyncio.sleep(INTER_CASE_DELAY_SECS)
            continue

        await asyncio.sleep(INTER_CASE_DELAY_SECS)
        rows.append(
            {
                "id": case["id"],
                "split": case["split"],
                "source_set": case["source_set"],
                "category": case.get("category", "?"),
                "task": case.get("task", "?"),
                "question": case["question"],
                "answer": answer,
                "tools_used": tools_used,
                "scores": verdict.scores,
                "mean": round(verdict.mean, 2),
                "passed": verdict.passed,
                "unsafe": verdict.unsafe,
                "reason": verdict.reason,
            }
        )
        flag = " UNSAFE" if verdict.unsafe else ""
        print(
            f"[{index:>2}/{len(cases)}] {case['id']:<8} {case['split']:<8} "
            f"mean={verdict.mean:.2f}{flag}",
            flush=True,
        )
        # Written after every case, not once at the end: a run that dies at
        # case 34 used to discard 33 completed cases along with it.
        out_path.write_text(
            json.dumps({"report": _report(rows, unjudged, args), "rows": rows}, indent=2)
        )

    report = _report(rows, unjudged, args)
    out_path.write_text(json.dumps({"report": report, "rows": rows}, indent=2))

    print()
    print(json.dumps(report, indent=2))
    if unjudged:
        print(
            f"\nWARNING: {len(unjudged)} case(s) could not be judged and are "
            f"excluded from every figure above: "
            f"{', '.join(item['id'] for item in unjudged)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
