"""Fetch the visa volumes of the Foreign Affairs Manual (9 FAM).

9 FAM is the State Department's consular guidance — how a visa officer at an
embassy is told to apply the law. Consular practice lives here and nowhere
else: interviews, 221(g) administrative processing, refusals under 214(b),
issuance procedures. None of it is in the CFR, and the USCIS policy manual
does not cover it either, so before this the agent answered consular questions
from whatever a web search happened to surface.

fam.state.gov is its own host. Unlike travel.state.gov and state.gov — both
behind Cloudflare bot protection that returns 403 even to a residential client
— it answers plain HTTP, and it exposes an undocumented tree endpoint that
lists every section, so the volume can be enumerated rather than crawled.

The tree covers 9 FAM subchapters 01-06, which is the visa core.
"""

from __future__ import annotations

import asyncio
import html
import re

from ingest.ecfr import Section, chunk
from ingest.http import build_client

TREE_URL = "https://fam.state.gov/api/Tree/GetTreeByVolumeId"
BASE = "https://fam.state.gov"
VOLUME = "09FAM"

#: The <title> carries the citation and heading: "9 FAM 302.1 (U) INELIGIBILITY
#: BASED ON ...". "(U)" is the unclassified marking, not part of the heading.
_TITLE = re.compile(r"^\s*(9\s*FAM\s+[\d.]+)\s*(?:\(U\))?\s*(.*)$", re.IGNORECASE | re.DOTALL)

_PARA = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")

#: Header/footer furniture repeats on every page and carries no substance.
_SKIP_CLASS = "HeaderFooterClassificationIndicator"
_SKIP_TEXT = {"unclassified", "sensitive but unclassified", "unclassified//fouo"}


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    text = html.unescape(text)
    return re.sub(r"[\s\u00a0]+", " ", text).strip()


def leaves(nodes: list[dict]) -> list[dict]:
    """Flatten the tree to the nodes that actually have a page."""
    found: list[dict] = []
    for node in nodes:
        if node.get("url"):
            found.append(node)
        found.extend(leaves(node.get("items") or []))
    return found


def parse_section(page: str) -> Section | None:
    """Turn one FAM page into a Section, or None if it carries no text.

    Citation comes from the page title, never reconstructed: for a legal
    assistant a wrong citation is worse than no citation.
    """
    title = re.search(r"<title>(.*?)</title>", page, re.DOTALL | re.IGNORECASE)
    raw_title = _clean(title.group(1)) if title else ""
    match = _TITLE.match(raw_title)
    if not match:
        return None
    citation, heading = match.group(1), match.group(2).strip()

    paragraphs: list[str] = []
    for attributes, body in _PARA.findall(page):
        if _SKIP_CLASS in attributes:
            continue
        text = _clean(body)
        if not text or text.lower() in _SKIP_TEXT or len(text) < 3:
            continue
        paragraphs.append(text)

    if not paragraphs:
        return None
    return Section(
        citation=citation,
        heading=heading,
        text="\n".join(paragraphs),
        paragraphs=paragraphs,
    )


async def fetch_volume(*, concurrency: int = 4) -> tuple[list[dict], list[str]]:
    """Download every 9 FAM section and return records, plus what failed.

    Politeness: a small pool, so the host sees a handful of parallel readers
    rather than a burst. A section that fails is recorded and skipped — a
    partial pack beats no pack.
    """
    async with build_client() as client:
        response = await client.get(TREE_URL, params={"Id": VOLUME})
        response.raise_for_status()
        sections = leaves(response.json())
    print(f"  9 FAM tree lists {len(sections)} sections")

    semaphore = asyncio.Semaphore(concurrency)
    records: list[dict] = []
    failures: list[str] = []

    async with build_client() as client:

        async def one(node: dict) -> tuple[list[dict], str | None]:
            async with semaphore:
                try:
                    page = await client.get(BASE + node["url"])
                    page.raise_for_status()
                except Exception as error:  # noqa: BLE001 - recorded, not swallowed
                    return [], f"{node['id']} ({type(error).__name__})"
            try:
                body = page.content.decode("utf-8")
            except UnicodeDecodeError:
                # The pages declare iso-8859-1 but are mostly ASCII.
                body = page.content.decode("latin-1", errors="replace")

            parsed = parse_section(body)
            if parsed is None:
                return [], f"{node['id']} (no text)"
            return [
                {
                    "citation": piece.citation,
                    "heading": piece.heading,
                    "text": piece.text,
                    "part": "9 FAM",
                    "about": "Consular guidance (US State Department, Foreign Affairs Manual)",
                    "source": "fam",
                }
                for piece in chunk(parsed)
            ], None

        results = await asyncio.gather(*(one(node) for node in sections))

    for pieces, failure in results:
        if failure:
            failures.append(failure)
        else:
            records.extend(pieces)

    tokens = sum(len(r["text"]) // 4 for r in records)
    print(f"  9 FAM -> {len(records)} chunks, ~{tokens:,} tokens, {len(failures)} failed")
    return records, failures
