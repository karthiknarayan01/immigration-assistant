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
from collections import defaultdict
from datetime import datetime, timezone

import yaml
from google import genai
from google.genai import types

from app.config import settings
from app.prompt import SYSTEM_INSTRUCTION
from app.tools.registry import _HANDLERS, _SCHEMAS
from evals.score import FACTORS, JUDGE_MODEL, judge

HERE = pathlib.Path(__file__).resolve().parent
EVAL_SETS = ("eval_set.yaml", "eval_set_reddit.yaml")
RESULTS = HERE / "results"

#: The agent in production is a native-audio Live model. Driving that model
#: through a full tool round-trip from a script proved unreliable — it issues
#: the call, speaks a filler, then ends the turn without consuming the result
#: — so the eval runs the same system prompt and the same tools through the
#: text API. This measures answer substance, tool use, hedging and safety.
#: It does not measure voice, turn-taking or latency.
EVAL_MODEL = "gemini-2.5-flash"

MAX_TOOL_ROUNDS = 4

#: Roughly half, assigned deterministically so the split cannot drift between
#: runs — and so nobody can quietly move a failing case into `tune`.
HOLDOUT_FRACTION = 0.5


def split_for(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < HOLDOUT_FRACTION else "tune"


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for name in EVAL_SETS:
        path = HERE / name
        if not path.exists():
            continue
        for case in yaml.safe_load(path.read_text()) or []:
            case["source_set"] = "reddit" if "reddit" in name else "handwritten"
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


class _Params:
    """Stands in for pipecat's FunctionCallParams outside a live pipeline."""

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None

    async def result_callback(self, value):
        self.result = value


async def ask(client: genai.Client, question: str) -> tuple[str, list[str]]:
    """Put one question to the agent, running any tools it calls for real."""
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

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.aio.models.generate_content(
            model=EVAL_MODEL, contents=contents, config=config
        )
        candidate = response.candidates[0] if response.candidates else None
        parts = (candidate.content.parts if candidate and candidate.content else None) or []

        calls = [part.function_call for part in parts if getattr(part, "function_call", None)]
        if not calls:
            text = "".join(part.text for part in parts if getattr(part, "text", None))
            return text.strip(), tools_used

        contents.append(candidate.content)
        reply_parts = []
        for call in calls:
            tools_used.append(call.name)
            handler = _HANDLERS.get(call.name)
            params = _Params(dict(call.args or {}))
            if handler:
                await handler(params)
            reply_parts.append(
                types.Part.from_function_response(
                    name=call.name, response=params.result or {"error": "no handler"}
                )
            )
        contents.append(types.Content(role="user", parts=reply_parts))

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
    for row in rows:
        by_category[row["category"]].append(row["mean"])

    return {
        "cases": len(rows),
        "mean": round(statistics.mean(means), 2),
        "pass_rate_pct": round(100 * sum(row["passed"] for row in rows) / len(rows)),
        "unsafe_pct": round(100 * sum(row["unsafe"] for row in rows) / len(rows)),
        "by_factor": by_factor,
        "by_category": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_category.items())
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=("tune", "holdout", "all"), default="all")
    args = parser.parse_args()

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

    rows = []
    for index, case in enumerate(cases, start=1):
        try:
            answer, tools_used = await ask(client, case["question"])
        except Exception as error:  # noqa: BLE001 - one bad case must not end the run
            answer, tools_used = "", [f"ERROR: {type(error).__name__}"]

        verdict = judge(client, case, answer)
        rows.append(
            {
                "id": case["id"],
                "split": case["split"],
                "source_set": case["source_set"],
                "category": case.get("category", "?"),
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

    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eval_model": EVAL_MODEL,
        "production_model": settings.gemini_model,
        "judge_model": JUDGE_MODEL,
        "overall": summarise(rows),
        "tune": summarise([r for r in rows if r["split"] == "tune"]),
        "holdout": summarise([r for r in rows if r["split"] == "holdout"]),
    }

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (RESULTS / f"{stamp}.json").write_text(json.dumps({"report": report, "rows": rows}, indent=2))

    print()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
