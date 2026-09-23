"""Download the stable regulations and build a local knowledge pack.

Run this on a laptop, not in the cloud. Two of the sources the agent would
most like to read block datacenter traffic, and one of them blocks residential
traffic too — measured 2026-09-23 from a home connection:

    uscis.gov            200  works
    ecfr.gov API         200  works, no key needed
    egov.uscis.gov       403  blocked, not a datacenter problem
    travel.state.gov     403  blocked, not a datacenter problem

So this runs offline and commits its output. The agent then reads a file
instead of a website, which also means the rules it quotes cannot change
underneath a conversation.

What goes in: the regulations that barely move — grace periods, unemployment
limits, status rules, employment authorisation. What stays out: fees,
processing times and the visa bulletin, which are exactly the numbers that go
stale, and which must keep coming from live search.

    PYTHONPATH=. uv run python scripts/build_knowledge_pack.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date

import httpx

from ingest.ecfr import Section, chunk, fetch_part

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "knowledge" / "cfr.json"
TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"

#: The parts that carry the rules users actually ask about. 8 CFR 214.2 alone
#: is ~170k tokens, so this is chunked rather than held whole.
PARTS = [
    (8, "214", "Nonimmigrant classes: H-1B, F-1, L-1, B-1/B-2, and their conditions"),
    (8, "274a", "Employment authorisation, including who may work and when"),
    (8, "245", "Adjustment of status to permanent resident"),
    (8, "248", "Change of nonimmigrant status"),
    # Added after evals showed the gaps. A question about whether a green card
    # can be revoked cannot be answered without the conditional-residence
    # rules, and advance parole travel cannot be answered safely without the
    # inadmissibility and parole provisions.
    (8, "216", "Conditional permanent residence and removal of conditions"),
    (8, "212", "Inadmissibility, waivers, advance parole and documentary requirements"),
    (8, "223", "Re-entry permits and refugee travel documents"),
]


async def latest_issue_date(title: int) -> str:
    """Ask eCFR which date it can actually serve.

    Today's date 404s: the API serves issue dates, and the most recent one
    lags the present by days. Hardcoding a date would work until it silently
    stopped being the newest.
    """
    async with httpx.AsyncClient(timeout=60.0, headers={"Accept-Encoding": "gzip"}) as client:
        response = await client.get(TITLES_URL)
        response.raise_for_status()
        for entry in response.json().get("titles", []):
            if entry.get("number") == title:
                return entry["latest_issue_date"]
    raise RuntimeError(f"eCFR lists no issue date for title {title}")


async def main() -> int:
    as_of = await latest_issue_date(8)
    print(f"Fetching CFR text at eCFR's latest issue date: {as_of}")
    print(f"(today is {date.today().isoformat()}; the API has no issue for it)\n")

    records: list[dict] = []
    failures: list[str] = []

    for title, part, description in PARTS:
        label = f"{title} CFR {part}"
        try:
            sections = await fetch_part(title=title, part=part, date=as_of)
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            print(f"  FAIL  {label:<14} {type(error).__name__}: {str(error)[:60]}")
            failures.append(label)
            continue

        chunks: list[Section] = []
        for section in sections:
            chunks.extend(chunk(section))

        tokens = sum(c.tokens_estimate for c in chunks)
        print(f"  ok    {label:<14} {len(sections):>3} sections -> {len(chunks):>4} chunks, ~{tokens:,} tokens")

        for piece in chunks:
            records.append({
                "citation": piece.citation,
                "heading": piece.heading,
                "text": piece.text,
                "part": f"{title} CFR {part}",
                "about": description,
            })

    if not records:
        print("\nNothing fetched. Not overwriting the existing pack.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "as_of": as_of,
        "source": "eCFR API (ecfr.gov), current text",
        # Stated in the file so anything reading it knows what it must not be
        # used for. Fees and processing times are deliberately absent.
        "excludes": "fees, processing times, visa bulletin — these go stale and must be searched live",
        "chunks": records,
    }, indent=2))

    total = sum(len(r["text"]) for r in records)
    print(f"\nWrote {len(records):,} chunks (~{total // 4:,} tokens) to {OUT.relative_to(OUT.parent.parent.parent)}")
    if failures:
        print(f"Incomplete: {', '.join(failures)} could not be fetched.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
