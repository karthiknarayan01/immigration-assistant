"""Google Search, reached through Gemini's grounding rather than an allowlist.

Gemini can search Google natively, but not while it is also holding function
declarations — Vertex refuses the combination outright:

    Multiple tools are supported only when they are all search tools.

So it is wrapped as a tool of our own. The agent keeps its function calling,
and this handler makes a separate, small Gemini call whose only job is to
search and come back with a grounded answer and its sources.

Why this exists at all: the curated allowlist could only find what was on a
list written in advance. Asked whether visa refusals had risen, or for H-1B
denial rates for Indian firms, it returned nationality-blind petition totals
and then said it could not find anything — because that reporting is real,
and simply is not on a .gov domain. Google finds it.

It also costs no third-party search quota. The call bills to Vertex, which is
where the credits are.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from google import genai
from google.genai import types
from loguru import logger

from app.config import settings

#: Small and fast. This call does not reason about immigration, it retrieves —
#: the agent does the thinking with what comes back.
SEARCH_MODEL = "gemini-2.5-flash"

#: Enough to answer, short enough to sit inside a voice turn.
_INSTRUCTION = (
    "Search the web and report what you find on the user's question about US "
    "immigration. Prefer recent, specific figures, and say when each figure is "
    "from. State plainly if sources disagree. Do not give advice, do not add "
    "caveats about consulting a lawyer, and do not pad — report only what the "
    "sources say. Be brief."
)


@dataclass
class GroundedAnswer:
    text: str
    sources: list[dict] = field(default_factory=list)

    @property
    def found_anything(self) -> bool:
        return bool(self.text.strip())


async def search(query: str, *, timeout_secs: float | None = None) -> GroundedAnswer:
    """Run one grounded Google search and return the result with its sources."""
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project_id,
        location=settings.google_cloud_location,
    )
    response = await client.aio.models.generate_content(
        model=SEARCH_MODEL,
        contents=query,
        config=types.GenerateContentConfig(
            system_instruction=_INSTRUCTION,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0,
        ),
    )

    sources: list[dict] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        metadata = getattr(candidates[0], "grounding_metadata", None)
        for chunk in (getattr(metadata, "grounding_chunks", None) or []):
            web = getattr(chunk, "web", None)
            if not web:
                continue
            # `uri` is a Vertex redirect rather than the real page, so the
            # domain in `title` is the only thing worth showing a user.
            sources.append({"site": getattr(web, "title", "") or "", "url": getattr(web, "uri", "") or ""})

    text = (getattr(response, "text", "") or "").strip()
    logger.info(f"google search '{query[:60]}' -> {len(text)} chars, {len(sources)} sources")
    return GroundedAnswer(text=text, sources=sources)
