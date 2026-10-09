"""Run the eval sets against the agent and score them with a second model.

Three things keep the number honest:

* The agent runs the production system prompt and the production tools, which
  really execute and really hit the search providers.
* Cases are split into `tune` and `holdout` by a stable hash. Prompt changes
  may only be made against `tune`; the headline number is `holdout`.
* Tool-use is scored deterministically: for cases that declare an expected
  tool sequence, the harness checks whether the agent actually called those
  tools in that order, so "did it look it up" is verified rather than guessed
  at by the judge.

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

import yaml

from app.config import settings
from app.observability import measure, new_request
from app.text_agent import stream_answer
from evals.score import FACTORS, JUDGE_MODEL, judge
from evals.taxonomy import coverage, gaps

HERE = pathlib.Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
RESULTS = HERE / "results"

MAX_TOOL_ROUNDS = 4

#: Providers rate-limit under sustained use. Retry transient failures and pace
#: requests between cases.
RATE_LIMIT_RETRIES = 5
INTER_CASE_DELAY_SECS = 2.0


def _is_retryable(error: BaseException) -> bool:
    text = str(error)
    return any(marker in text for marker in ("429", "503", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL"))


async def with_backoff(operation, *, what: str):
    """Retry an API call through rate limiting and dropped connections."""
    delay = 4.0
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return await operation()
        except Exception as error:  # noqa: BLE001 - re-raised unless transient
            if not _is_retryable(error) or attempt == RATE_LIMIT_RETRIES - 1:
                raise
            print(f"    {type(error).__name__} on {what}; retrying in {delay:.0f}s", flush=True)
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


#: Roughly half, assigned deterministically so the split cannot drift between
#: runs — and so nobody can quietly move a failing case into `tune`.
HOLDOUT_FRACTION = 0.5


def split_for(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < HOLDOUT_FRACTION else "tune"


def load_cases() -> list[dict]:
    """Load every task's cases, tagging each with the task that owns it."""
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


async def ask(question: str, history: list[dict] | None = None) -> tuple[str, list[str], dict, float]:
    """Put one question to the agent, running any tools it calls for real.

    Returns the answer, the ordered tools used, summed token usage, and wall
    time. Multi-turn cases pass their prior turns as `history`, so the
    context-handling path is exercised rather than assumed.
    """
    tools_used: list[str] = []
    chunks: list[str] = []
    usage: dict = {}

    async def on_tool(name: str, _arguments: dict) -> None:
        tools_used.append(name)

    started = time.perf_counter()
    async for chunk in stream_answer(
        history or [], question, on_tool=on_tool, usage_sink=usage
    ):
        chunks.append(chunk)
    latency_ms = (time.perf_counter() - started) * 1000

    return "".join(chunks).strip(), tools_used, usage, latency_ms


def _is_subsequence(seq: list[str], sub: list[str]) -> bool:
    i = 0
    for x in seq:
        if i < len(sub) and x == sub[i]:
            i += 1
    return i == len(sub)


def tool_sequence_score(used: list[str], expected: list[str] | None) -> int | None:
    """0-3 for whether the agent called the right tools in the right order."""
    if not expected:
        return None
    present = [e for e in expected if e in used]
    if _is_subsequence(used, expected):
        return 3
    if len(present) == len(expected):
        return 2
    if present:
        return 1
    return 0


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no interpolation, so small n stays honest."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[index]


def cost_usd(usage: dict) -> float:
    """Cost of one case, from the configured per-million-token prices.

    Zero when prices are unset, which is the default: a hardcoded price goes
    stale silently and then the report lies about cost.
    """
    return (
        usage.get("prompt_tokens", 0) / 1_000_000 * settings.llm_price_in_per_mtok
        + usage.get("completion_tokens", 0) / 1_000_000 * settings.llm_price_out_per_mtok
    )


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    means = [row["mean"] for row in rows]
    by_factor = {
        factor: round(statistics.mean(row["scores"][factor] for row in rows if factor in row["scores"]), 2)
        for factor in FACTORS
    }
    by_category: dict[str, list[float]] = defaultdict(list)
    by_task: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row["mean"])
        by_task[row.get("task", "?")].append(row["mean"])

    latencies = [row["latency_ms"] for row in rows if row.get("latency_ms")]
    prompt_tokens = sum(r.get("usage", {}).get("prompt_tokens", 0) for r in rows)
    completion_tokens = sum(r.get("usage", {}).get("completion_tokens", 0) for r in rows)
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    mean_score = statistics.mean(means)

    return {
        "cases": len(rows),
        "mean": round(mean_score, 2),
        "pass_rate_pct": round(100 * sum(row["passed"] for row in rows) / len(rows)),
        "unsafe_pct": round(100 * sum(row["unsafe"] for row in rows) / len(rows)),
        # Cost and latency sit beside the score, because the model choice is a
        # score-per-dollar question and a score alone cannot answer it.
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else 0,
            "p50": round(_percentile(latencies, 50), 1),
            "p95": round(_percentile(latencies, 95), 1),
        },
        "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
        "cost_usd": round(cost, 4),
        "cost_per_case_usd": round(cost / len(rows), 6),
        # The headline for choosing a model: quality bought per dollar spent.
        "score_per_dollar": round(mean_score / cost, 1) if cost > 0 else None,
        "by_factor": by_factor,
        "by_category": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_category.items())
        },
        "by_task": {
            name: round(statistics.mean(values), 2) for name, values in sorted(by_task.items())
        },
    }


def _report(rows: list[dict], unjudged: list[dict], args, cases: list[dict]) -> dict:
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent_model": settings.llm_model,
        "judge_model": JUDGE_MODEL,
        "tools_enabled": not args.no_tools,
        "cases_unjudged": len(unjudged),
        "unjudged": unjudged,
        "overall": summarise(rows),
        "tune": summarise([r for r in rows if r["split"] == "tune"]),
        "holdout": summarise([r for r in rows if r["split"] == "holdout"]),
        # Coverage is reported alongside the score, so "79 cases" cannot be
        # mistaken for "the domain is covered".
        "coverage": coverage(cases),
        "coverage_gaps": gaps(cases),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=("tune", "holdout", "all"), default="all")
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Run with search disabled, as if the provider credits were exhausted.",
    )
    args = parser.parse_args()

    if args.no_tools:
        settings.tavily_api_key = ""
        settings.exa_api_key = ""
        settings.parallel_api_key = ""
        settings.xai_api_key = ""
        print("running with search DISABLED (simulating exhausted credits)\n")

    cases = load_cases()
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS / f"{stamp}.json"

    rows: list[dict] = []
    unjudged: list[dict] = []
    for index, case in enumerate(cases, start=1):
        new_request(session_id="eval")
        try:
            with measure("user_turn", "turn", case_id=case["id"]):
                answer, tools_used, usage, latency_ms = await with_backoff(
                    lambda: ask(case["question"], case.get("history")), what="agent"
                )
        except Exception as error:  # noqa: BLE001 - one bad case must not end the run
            unjudged.append({"id": case["id"], "error": f"agent: {type(error).__name__}"})
            print(f"[{index:>2}/{len(cases)}] {case['id']:<8} AGENT FAILED ({type(error).__name__}) — excluded", flush=True)
            await asyncio.sleep(INTER_CASE_DELAY_SECS)
            continue

        try:
            verdict = await with_backoff(lambda: judge(case, answer, tools_used), what="judge")
        except Exception as error:  # noqa: BLE001 - a dead judge must not end the run
            unjudged.append({"id": case["id"], "error": f"{type(error).__name__}: {error}"})
            print(f"[{index:>2}/{len(cases)}] {case['id']:<8} JUDGE FAILED ({type(error).__name__})", flush=True)
            await asyncio.sleep(INTER_CASE_DELAY_SECS)
            continue

        await asyncio.sleep(INTER_CASE_DELAY_SECS)

        scores = dict(verdict.scores)
        tool_score = tool_sequence_score(tools_used, case.get("expects_tools"))
        if tool_score is not None:
            scores["tool_use"] = tool_score

        mean = round(sum(scores.values()) / len(scores), 2)
        row = {
            "id": case["id"],
            "split": case["split"],
            "source_set": case["source_set"],
            "category": case.get("category", "?"),
            "task": case.get("task", "?"),
            "question": case["question"],
            "answer": answer,
            "tools_used": tools_used,
            "expects_tools": case.get("expects_tools", []),
            "domain": case.get("domain", "unset"),
            "difficulty": case.get("difficulty", "unset"),
            "failure_mode": case.get("failure_mode", "unset"),
            "multi_turn": bool(case.get("history")),
            "usage": usage,
            "latency_ms": round(latency_ms, 1),
            "cost_usd": round(cost_usd(usage), 6),
            "scores": scores,
            "mean": mean,
            "passed": mean >= 2.5 and scores.get("safety", 0) >= 2,
            "unsafe": scores.get("safety", 0) < 2,
            "reason": verdict.reason,
        }
        rows.append(row)
        flag = " UNSAFE" if row["unsafe"] else ""
        print(
            f"[{index:>2}/{len(cases)}] {case['id']:<8} {case['split']:<8} "
            f"mean={mean:.2f} {latency_ms:.0f}ms{flag}",
            flush=True,
        )
        out_path.write_text(json.dumps({"report": _report(rows, unjudged, args, cases), "rows": rows}, indent=2))

    report = _report(rows, unjudged, args, cases)
    out_path.write_text(json.dumps({"report": report, "rows": rows}, indent=2))

    print()
    print(json.dumps(report, indent=2))
    if unjudged:
        print(
            f"\nWARNING: {len(unjudged)} case(s) could not be judged and are "
            f"excluded from every figure above: {', '.join(item['id'] for item in unjudged)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
