"""Run the generated factual set against the agent, scoring without a judge.

Every case here carries `must_contain`: tokens taken from the source text and
verified to appear in it. So the score is a plain text match — no model, no
rubric, no drift. That is the whole point of generating this set rather than
hand-writing it.

    PYTHONPATH=. uv run python evals/run_generated.py [--domains h1b,f1_opt] [--limit 20]

Reports accuracy per domain, because an overall mean over twelve domains hides
whether one of them is broken.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import statistics
import time

from evals.run_eval import ask

HERE = pathlib.Path(__file__).parent
CASES = HERE / "tasks_generated"


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def has_token(answer: str, token: str) -> bool:
    """Is this token stated? Loose on formatting, strict on substance."""
    haystack = normalise(answer)
    needle = normalise(token)
    if needle in haystack:
        return True
    # "103,265" and "103265" are the same figure.
    if needle.replace(",", "") in haystack.replace(",", ""):
        return True
    return False


def load(domains: list[str]) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES.glob("*.json")):
        payload = json.loads(path.read_text())
        if domains and payload["domain"] not in domains:
            continue
        cases.extend(payload["cases"])
    return cases


async def one(case: dict) -> dict:
    started = time.perf_counter()
    try:
        answer, _, _, _, _ = await ask(case["question"])
    except Exception as error:  # noqa: BLE001 - one bad case is not fatal
        answer = f"__error__ {type(error).__name__}"
    latency = (time.perf_counter() - started) * 1000
    stated = [t for t in case["must_contain"] if has_token(answer, t)]
    missing = [t for t in case["must_contain"] if not has_token(answer, t)]
    return {
        "id": case.get("id", case["question"][:30]),
        "domain": case["domain"],
        "question": case["question"],
        "answer": answer,
        "stated": stated,
        "missing": missing,
        "accurate": not missing,
        "partial": round(len(stated) / len(case["must_contain"]), 3),
        "latency_ms": round(latency, 1),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domains", default="")
    parser.add_argument("--limit", type=int, default=0, help="per domain, 0 = all")
    parser.add_argument("--out", type=pathlib.Path, default=HERE / "results" / "generated.json")
    args = parser.parse_args()

    wanted = [d.strip() for d in args.domains.split(",") if d.strip()]
    cases = load(wanted)
    if args.limit:
        kept, seen = [], {}
        for case in cases:
            seen[case["domain"]] = seen.get(case["domain"], 0)
            if seen[case["domain"]] < args.limit:
                kept.append(case)
                seen[case["domain"]] += 1
        cases = kept

    print(f"running {len(cases)} generated cases")
    rows = []
    for index, case in enumerate(cases, 1):
        rows.append(await one(case))
        if index % 25 == 0:
            acc = sum(r["accurate"] for r in rows) / len(rows)
            print(f"  {index}/{len(cases)}  accurate so far {acc:.1%}")

    by_domain: dict[str, list[dict]] = {}
    for row in rows:
        by_domain.setdefault(row["domain"], []).append(row)

    report = {
        "cases": len(rows),
        "accurate_pct": round(100 * sum(r["accurate"] for r in rows) / len(rows), 1),
        "partial_mean": round(statistics.mean(r["partial"] for r in rows), 3),
        "median_latency_ms": round(statistics.median(r["latency_ms"] for r in rows), 1),
        "by_domain": {
            domain: {
                "cases": len(bucket),
                "accurate_pct": round(100 * sum(r["accurate"] for r in bucket) / len(bucket), 1),
            }
            for domain, bucket in sorted(by_domain.items())
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"report": report, "rows": rows}, indent=2))

    print()
    print(f"accurate: {report['accurate_pct']}%   partial: {report['partial_mean']}   median {report['median_latency_ms']}ms")
    for domain, stats in report["by_domain"].items():
        print(f"  {domain:16} {stats['accurate_pct']:>5}%  ({stats['cases']} cases)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
