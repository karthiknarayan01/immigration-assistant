"""Harvest real user questions from immigration forums.

Hand-written eval questions measure what the author imagined users ask.
Real ones carry the actual phrasing, the buried assumptions, and the
half-specified situations that make this domain hard.

Only the *questions* are taken. Forum answers are never used as ground
truth — they are precisely the unreliable material the source tiering exists
to contain, and grading against them would encode folklore as correctness.

Usage:
    PYTHONPATH=. uv run python evals/harvest_reddit.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re

import httpx

from app.config import settings

OUT = pathlib.Path(__file__).resolve().parent / "harvested.json"

# Spread across visa types and life events rather than chasing one topic, so
# the resulting set is not all H-1B.
SEEDS = [
    "H-1B transfer questions",
    "H-1B RFE what happened",
    "H-1B layoff grace period question",
    "F-1 OPT question about unemployment days",
    "F-1 STEM OPT extension question",
    "F-1 to H-1B cap gap question",
    "green card EB-2 priority date question",
    "EB-1A petition question",
    "I-485 adjustment of status question",
    "advance parole travel question",
    "H-4 EAD question",
    "B-2 visa extension question",
    "L-1 visa transfer question",
    "naturalization N-400 question",
    "visa interview 221g administrative processing question",
    "I-140 revoked question",
    "H-1B cap exempt question",
    "marriage green card interview question",
]

QUESTION_HINTS = (
    "?",
    "can i",
    "should i",
    "do i",
    "will i",
    "what happens",
    "how long",
    "is it",
    "am i",
    "does anyone",
)


def looks_like_a_question(title: str) -> bool:
    lowered = title.lower()
    return any(hint in lowered for hint in QUESTION_HINTS)


def clean(title: str) -> str:
    # Forum titles are littered with [tags] and subreddit prefixes.
    title = re.sub(r"^\s*\[[^\]]{1,24}\]\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip(" -–—|")
    return title.strip()


async def harvest_one(client: httpx.AsyncClient, seed: str) -> list[dict]:
    response = await client.post(
        "https://api.parallel.ai/v1/search",
        headers={"x-api-key": settings.parallel_api_key},
        json={
            "objective": (
                "Real questions asked by individuals about their own US "
                "immigration situation"
            ),
            "search_queries": [f"site:reddit.com {seed}"],
            "mode": "fast",
        },
    )
    response.raise_for_status()

    found = []
    for item in response.json().get("results", []):
        url = item.get("url") or ""
        if "reddit.com" not in url or "/comments/" not in url:
            continue
        title = clean(item.get("title") or "")
        if len(title) < 25 or not looks_like_a_question(title):
            continue
        found.append(
            {
                "title": title,
                "url": url,
                "seed": seed,
                "excerpt": " ".join(item.get("excerpts") or [])[:600],
            }
        )
    return found


async def main() -> None:
    if not settings.parallel_api_key:
        raise SystemExit("PARALLEL_API_KEY is not set")

    seen: set[str] = set()
    harvested: list[dict] = []

    async with httpx.AsyncClient(timeout=60) as client:
        results = await asyncio.gather(
            *(harvest_one(client, seed) for seed in SEEDS), return_exceptions=True
        )

    for result in results:
        if isinstance(result, BaseException):
            print(f"  seed failed: {result!r}")
            continue
        for item in result:
            key = item["url"].split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            harvested.append(item)

    OUT.write_text(json.dumps(harvested, indent=2))
    print(f"harvested {len(harvested)} candidate questions -> {OUT.name}")
    for item in harvested[:15]:
        print(f"  - {item['title'][:95]}")


if __name__ == "__main__":
    asyncio.run(main())
