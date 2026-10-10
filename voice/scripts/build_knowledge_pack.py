"""Download the stable regulations and build a local knowledge pack.

Run this on a laptop, not in the cloud. Several of the sources the agent would
most like to read block datacenter traffic, and two block plain HTTP clients
outright — measured 2026-10:

    ecfr.gov API         200  works, no key needed
    fam.state.gov        200  works (its own host, not behind the block below)
    travel.state.gov     403  Cloudflare bot protection, even residentially
    state.gov            403  same
    uscis.gov            200  from a home connection

So this runs offline and commits its output. The agent then reads a file
instead of a website, which also means the rules it quotes cannot change
underneath a conversation.

What goes in: the regulations that barely move — status rules, grace periods,
unemployment limits, work authorisation, consular processing, and the Foreign
Affairs Manual's visa volumes. What stays out: fees, processing times and the
visa bulletin, which are exactly the numbers that go stale and must keep
coming from live search.

    PYTHONPATH=. uv run python scripts/build_knowledge_pack.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date

import httpx

from ingest.ecfr import Section, chunk, fetch_part
from ingest.fam import fetch_volume as fetch_fam
from ingest.uscis_policy_manual import fetch_manual as fetch_policy_manual

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "knowledge" / "cfr.json"
TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"

#: The parts that carry the rules users actually ask about.
#:
#: 8 CFR is DHS — petitions, status, work authorisation. 22 CFR is the State
#: Department's visa regulations, which govern what happens at a consulate
#: (interviews, refusals, 221(g), issuance) and were missing entirely before:
#: every consular question was answered from whatever a web search happened to
#: return. 20 CFR carries the Labor Department's PERM rules.
#:
#: The builder skips a part that fails rather than abandoning the run, so a
#: stale part number costs one line of output, not the pack.
PARTS = [
    # ── 8 CFR: DHS ──────────────────────────────────────────────────────────
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
    # ── 22 CFR: State Department (visas) ────────────────────────────────────
    (22, "40", "Basis for refusing a visa: grounds of inadmissibility at consulates"),
    (22, "41", "Nonimmigrant visas: application, issuance and refusal"),
    (22, "42", "Immigrant visas: petition-based and numerical-limit processing"),
    (22, "46", "Control of issuance of visas and other documentation"),
    (22, "47", "Abandonment of status by an alien seaman"),
    (22, "50", "Nationality procedures: determination and documentation"),
    (22, "51", "Passports"),
    (22, "62", "Exchange Visitor Program (J-1)"),
    # ── 20 CFR: Labor Department ────────────────────────────────────────────
    (20, "655", "Labor certification (PERM), H-2 and related employment rules"),
]


async def title_issue_dates() -> dict[int, str]:
    """Ask eCFR which date it can serve for each title it has.

    Today's date 404s: the API serves issue dates, and the most recent one lags
    the present by days. Titles update on their own schedules, so the date is
    looked up per title rather than taken once — a hardcoded date would work
    until it silently stopped being the newest.
    """
    async with httpx.AsyncClient(timeout=60.0, headers={"Accept-Encoding": "gzip"}) as client:
        response = await client.get(TITLES_URL)
        response.raise_for_status()
        return {
            entry["number"]: entry["latest_issue_date"]
            for entry in response.json().get("titles", [])
            if entry.get("number") is not None
        }


async def ecfr_records() -> tuple[list[dict], list[str]]:
    dates = await title_issue_dates()
    print("eCFR issue dates: " + ", ".join(f"title {t}={d}" for t, d in sorted(dates.items()) if t in {p[0] for p in PARTS}))
    print(f"(today is {date.today().isoformat()}; the API has no issue for it)\n")

    records: list[dict] = []
    failures: list[str] = []

    for title, part, description in PARTS:
        label = f"{title} CFR {part}"
        issue_date = dates.get(title)
        if not issue_date:
            print(f"  SKIP  {label:<14} eCFR lists no issue date for title {title}")
            failures.append(label)
            continue
        try:
            sections = await fetch_part(title=title, part=part, date=issue_date)
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
                "part": label,
                "about": description,
                "source": "ecfr",
            })

    return records, failures


async def main() -> int:
    records: list[dict] = []
    failures: list[str] = []

    print("── eCFR ──────────────────────────────────────────────────────────")
    ecfr, ecfr_failures = await ecfr_records()
    records += ecfr
    failures += ecfr_failures

    print("\n── 9 FAM (consular) ──────────────────────────────────────────────")
    try:
        fam, fam_failures = await fetch_fam()
        records += fam
        failures += fam_failures
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"  FAIL  9 FAM {type(error).__name__}: {str(error)[:70]}")
        failures.append("9 FAM")

    print("\n── USCIS Policy Manual (agency guidance) ─────────────────────────")
    try:
        manual, manual_failures = await fetch_policy_manual()
        records += manual
        failures += manual_failures
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"  FAIL  USCIS Policy Manual {type(error).__name__}: {str(error)[:70]}")
        failures.append("USCIS Policy Manual")

    if not records:
        print("\nNothing fetched. Not overwriting the existing pack.")
        return 1

    as_of = date.today().isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        # Stated in the file so anything reading it knows the provenance and
        # what it must not be used for. Fees and processing times are
        # deliberately absent.
        "as_of": as_of,
        "source": (
            "eCFR API (8/20/22 CFR), fam.state.gov (9 FAM consular guidance), "
            "and the USCIS Policy Manual"
        ),
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
