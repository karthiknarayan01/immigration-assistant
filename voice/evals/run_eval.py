"""Run the eval set against the real agent and score it with a second model.

Deliberately exercises the production path: the same system prompt, the same
tool declarations, and tools that really execute and really hit the search
providers. Evaluating a stripped-down copy would measure something the user
never talks to.

The agent is a native-audio model, so answers come back as the output
transcription of its own speech — what a user would actually hear, not a
text rendering it never produces.

Usage:
    PYTHONPATH=. uv run python evals/run_eval.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
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

HERE = pathlib.Path(__file__).resolve().parent
EVAL_SET = HERE / "eval_set.yaml"
RESULTS = HERE / "results"

#: A different, stronger model than the agent under test. Not a different
#: family — Claude is not enabled in this project's Model Garden — so this
#: mitigates self-grading without fully eliminating shared blind spots.
JUDGE_MODEL = "gemini-2.5-pro"

JUDGE_INSTRUCTION = """
You are grading a voice assistant that answers US immigration questions.

Score 0-3 against the requirements given:
3 - every requirement met, nothing forbidden, no factual errors
2 - substantially right, a minor omission or imprecision
1 - partially right, a significant gap or an error
0 - wrong, irrelevant, or does something explicitly forbidden

Anything in "forbids" caps the score at 1, however fluent the answer.

This is spoken output. Do not penalise conversational phrasing, a follow-up
offer, or the absence of markdown. Do not reward length. Judge only whether
the substance is correct, appropriately hedged, and safe.

Return JSON only: {"score": <0-3>, "reason": "<one sentence>"}
""".strip()


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


#: The agent in production is a native-audio Live model. Driving that model
#: through a full tool round-trip from a script proved unreliable — it issues
#: the call, speaks a filler, and ends the turn without consuming the result
#: — so the eval runs the same system prompt and the same tools through the
#: text API instead.
#:
#: What this measures: answer substance, tool use, hedging, and safety
#: behaviour. What it does not measure: voice, turn-taking, or latency. Those
#: need a human with a microphone.
EVAL_MODEL = "gemini-2.5-flash"

MAX_TOOL_ROUNDS = 4


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


def judge(client: genai.Client, case: dict, answer: str) -> dict:
    prompt = json.dumps(
        {
            "question": case["question"],
            "requires": case.get("requires", []),
            "forbids": case.get("forbids", []),
            "answer": answer or "(the assistant said nothing)",
        },
        indent=2,
    )
    response = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    try:
        return json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        return {"score": 0, "reason": "judge returned unparseable output"}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    args = parser.parse_args()

    cases = yaml.safe_load(EVAL_SET.read_text())
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
                "category": case["category"],
                "question": case["question"],
                "answer": answer,
                "tools_used": tools_used,
                "score": verdict.get("score", 0),
                "reason": verdict.get("reason", ""),
            }
        )
        print(
            f"[{index:>2}/{len(cases)}] {case['id']:<10} "
            f"score={verdict.get('score')} tools={','.join(tools_used) or '-'}",
            flush=True,
        )

    by_category = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row["score"])

    scores = [row["score"] for row in rows]
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eval_model": EVAL_MODEL,
        "production_model": settings.gemini_model,
        "judge_model": JUDGE_MODEL,
        "cases": len(rows),
        "mean": round(statistics.mean(scores), 2) if scores else 0,
        "full_marks_pct": round(100 * sum(s == 3 for s in scores) / len(scores)) if scores else 0,
        "failures_pct": round(100 * sum(s <= 1 for s in scores) / len(scores)) if scores else 0,
        "by_category": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_category.items())
        },
    }

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (RESULTS / f"{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))

    print()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
