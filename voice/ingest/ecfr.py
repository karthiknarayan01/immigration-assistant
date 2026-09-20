"""Fetch and extract regulation text from the eCFR API.

Runs offline, not during a conversation. uscis.gov's processing-times
subdomain and travel.state.gov block datacenter traffic, but eCFR exposes a
proper public API with no key, so primary regulatory text comes from here.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field

import httpx

ECFR_FULL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{title}.xml"

# The API rejects uncompressed requests with a 406.
_HEADERS = {"Accept-Encoding": "gzip, deflate"}


@dataclass
class Section:
    citation: str
    heading: str
    text: str
    #: Kept because eCFR XML carries no paragraph hierarchy — every <P> is
    #: flat — so paragraph boundaries are the only structural signal left.
    paragraphs: list[str] = field(default_factory=list)

    @property
    def tokens_estimate(self) -> int:
        """Rough token count — enough to budget the context pack against."""
        return len(self.text) // 4


def _clean(fragment: str) -> str:
    """XML fragment to readable prose, preserving sentence spacing."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    # The regs are full of "( a )" artefacts once tags are stripped.
    text = re.sub(r"\(\s+([a-zA-Z0-9]+)\s+\)", r"(\1)", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def parse_sections(xml: str) -> list[Section]:
    """Split eCFR XML into sections, keeping each one's official citation.

    Citations are preserved rather than reconstructed: for a legal assistant
    a wrong citation is worse than no citation, and the API already provides
    the authoritative one in hierarchy_metadata.
    """
    sections: list[Section] = []

    for match in re.finditer(
        r'<DIV8\b([^>]*)>(.*?)</DIV8>', xml, re.DOTALL
    ):
        attrs, body = match.group(1), match.group(2)

        citation = ""
        meta = re.search(r'hierarchy_metadata="([^"]*)"', attrs)
        if meta:
            try:
                citation = json.loads(html.unescape(meta.group(1))).get("citation", "")
            except (json.JSONDecodeError, AttributeError):
                citation = ""
        if not citation:
            number = re.search(r'N="([^"]+)"', attrs)
            citation = f"8 CFR {number.group(1)}" if number else "8 CFR"

        head = re.search(r"<HEAD>(.*?)</HEAD>", body, re.DOTALL)
        heading = _clean(head.group(1)) if head else ""
        # Strip the heading so it isn't repeated inside the body text.
        body_without_head = re.sub(r"<HEAD>.*?</HEAD>", "", body, count=1, flags=re.DOTALL)

        paragraphs = [
            _clean(p) for p in re.findall(r"<P>(.*?)</P>", body_without_head, re.DOTALL)
        ]
        kept = [p for p in paragraphs if p]
        text = "\n".join(kept)
        if text:
            sections.append(
                Section(citation=citation, heading=heading, text=text, paragraphs=kept)
            )

    return sections


async def fetch_part(
    *, title: int, part: str, date: str, section: str | None = None, timeout: float = 120.0
) -> list[Section]:
    """Download one CFR part (optionally a single section) and parse it."""
    params: dict[str, str] = {"part": part}
    if section:
        params["section"] = section

    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
        response = await client.get(
            ECFR_FULL.format(date=date, title=title), params=params
        )
        response.raise_for_status()
        return parse_sections(response.text)


def to_records(sections: list[Section]) -> list[dict]:
    return [asdict(s) for s in sections]


#: 8 CFR 214.2 defines every nonimmigrant class in one ~170k-token section.
#: Splitting it on top-level paragraph letters looks attractive but is not
#: reliably possible: eCFR XML carries no paragraph hierarchy (every <P> is
#: flat), and nested roman numerals are textually indistinguishable from
#: top-level letters — "(i) Licensure for H classification —(A) General" has
#: the same shape as a real "(b) Visitors —(1) General". Attempts using
#: letter sequencing and em-dash titles both mis-split, silently moving most
#: of the H-1B rules under paragraph (i).
#:
#: Retrieval does not need semantic boundaries, only right-sized chunks that
#: carry a correct citation, so chunking by size is used instead. A wrong
#: citation would be worse than a coarse one.

CHUNK_TOKENS = 700
CHUNK_OVERLAP_TOKENS = 80


def chunk(section: Section, *, size: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS
          ) -> list[Section]:
    """Break a section into overlapping chunks on paragraph boundaries.

    Never splits mid-paragraph, so a chunk is always readable prose. The
    overlap carries context across boundaries so a rule split across two
    chunks is still retrievable from either.
    """
    paragraphs = section.paragraphs or [section.text]
    chunks: list[Section] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> None:
        if not current:
            return
        chunks.append(
            Section(
                citation=section.citation,
                heading=section.heading,
                text="\n".join(current).strip(),
                paragraphs=list(current),
            )
        )

    for paragraph in paragraphs:
        tokens = max(1, len(paragraph) // 4)
        if current and current_tokens + tokens > size:
            flush()
            # Re-seed with trailing paragraphs up to the overlap budget.
            carried: list[str] = []
            carried_tokens = 0
            for previous in reversed(current):
                previous_tokens = max(1, len(previous) // 4)
                if carried_tokens + previous_tokens > overlap:
                    break
                carried.insert(0, previous)
                carried_tokens += previous_tokens
            current = carried
            current_tokens = carried_tokens
        current.append(paragraph)
        current_tokens += tokens

    flush()
    return chunks
