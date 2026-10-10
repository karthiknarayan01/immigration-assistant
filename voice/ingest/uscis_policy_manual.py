"""Fetch the USCIS Policy Manual.

The Policy Manual is USCIS's binding guidance to its own officers — how the
agency reads its regulations in practice — and it settles questions the CFR
leaves open. It is the single biggest gap in a corpus built only from
regulations.

There is no API, and the site is a Drupal book, but the book module exposes a
"printer friendly" export of every volume in one GET, which turns what would
be a 667-page crawl into one request.

Structure: `h1.book-node-heading-depth-{2,3,4}` marks Volume, Part and Chapter.
A chapter is the unit worth citing, so each becomes a Section whose citation
carries its volume and part, and is then chunked like everything else.
"""

from __future__ import annotations

import html
import re

from ingest.ecfr import Section, chunk
from ingest.http import build_client

EXPORT_URL = "https://www.uscis.gov/book/export/html/68600"

#: Depth 2 = Volume, 3 = Part, 4 = Chapter. Any other depth is site chrome.
_HEADING = re.compile(
    r'<h1 class="book-node-heading book-node-heading-depth-(\d)"[^>]*>(.*?)</h1>',
    re.DOTALL | re.IGNORECASE,
)
_PARA = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")

#: The export carries the site's navigation and a table of contents.
_SKIP_TEXT = {
    "policy manual",
    "about the policy manual",
    "table of contents",
    "search",
    "updates",
    "footnotes",
}


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    text = html.unescape(text)
    return re.sub(r"[\s\u00a0]+", " ", text).strip()


def _short(title: str) -> str:
    """'Volume 2 - Nonimmigrants' -> 'Volume 2'."""
    return title.split(" - ", 1)[0].strip()


def parse_manual(page: str) -> list[Section]:
    """Split the book export into chapters, carrying volume and part."""
    marks = list(_HEADING.finditer(page))
    volume = part = ""
    sections: list[Section] = []

    for index, mark in enumerate(marks):
        depth = int(mark.group(1))
        title = _clean(mark.group(2))
        end = marks[index + 1].start() if index + 1 < len(marks) else len(page)

        if depth == 2:
            # A new volume resets the part, so a later volume cannot inherit
            # the previous volume's part letter.
            volume, part = _short(title), ""
            continue
        if depth == 3:
            part = _short(title)
            continue
        if depth != 4 or title.lower() in _SKIP_TEXT:
            continue

        body = page[mark.end():end]
        paragraphs = [cleaned for cleaned in (_clean(p) for p in _PARA.findall(body)) if len(cleaned) >= 3]
        if not paragraphs:
            continue

        citation = ", ".join(bit for bit in (volume, part, title) if bit)
        sections.append(
            Section(citation=citation, heading=title, text="\n".join(paragraphs), paragraphs=paragraphs)
        )

    return sections


async def fetch_manual() -> tuple[list[dict], list[str]]:
    async with build_client() as client:
        response = await client.get(EXPORT_URL)
        response.raise_for_status()
        page = response.text

    sections = parse_manual(page)
    print(f"  Policy Manual: {len(sections)} chapters from one export")

    records: list[dict] = []
    for section in sections:
        for piece in chunk(section):
            records.append({
                "citation": piece.citation,
                "heading": piece.heading,
                "text": piece.text,
                "part": "USCIS Policy Manual",
                "about": "USCIS guidance to its officers: how the agency applies the regulations",
                "source": "uscis_pm",
            })

    tokens = sum(len(r["text"]) // 4 for r in records)
    print(f"  Policy Manual -> {len(records)} chunks, ~{tokens:,} tokens")
    return records, []
