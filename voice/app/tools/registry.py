"""Tools exposed to the model.

Deliberately few. Every extra tool is another thing the model can pick wrongly
mid-conversation, and every tool call costs seconds of a voice turn. The deep
research, academic, and YouTube tools are intentionally absent: at 10-30s they
belong in the text path, not a live conversation.
"""

from datetime import datetime, timezone

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from app.config import settings
from app.tools import providers
from app.tools.credibility import Anecdote, filter_anecdotes
from app.tools.sources import OFFICIAL_SEARCH_DOMAINS, SourceTier

MAX_SPOKEN_HITS = 4


def _format_official(hits) -> dict:
    results = []
    for hit in hits[:MAX_SPOKEN_HITS]:
        results.append({
            "title": hit.title,
            "url": hit.url,
            "excerpt": hit.text[:600],
            "published": hit.published.date().isoformat() if hit.published else "undated",
            # The model is told to treat these tiers differently, so it has to
            # be able to see which is which.
            "trust": hit.tier.value,
        })
    return {"results": results, "count": len(results)}


async def search_official_guidance(params: FunctionCallParams):
    """Authoritative-first search: government sources and the immigration bar."""
    query = str(params.arguments.get("query", "")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    if not providers.available_providers():
        await params.result_callback({
            "unavailable": True,
            "message": (
                "Search is not configured. Answer from your own knowledge, say "
                "explicitly that you could not check a live source, and suggest "
                "confirming on uscis.gov."
            ),
        })
        return

    hits = await providers.search(query, domains=OFFICIAL_SEARCH_DOMAINS, limit=6)
    official = [h for h in hits if h.tier in (SourceTier.AUTHORITATIVE, SourceTier.PROFESSIONAL)]
    logger.info(f"official search '{query}' -> {len(official)} usable hits")
    await params.result_callback(_format_official(official))


async def search_community_experiences(params: FunctionCallParams):
    """What people report in practice — filtered, and never stated as fact."""
    query = str(params.arguments.get("query", "")).strip()
    topic = str(params.arguments.get("topic", "policy")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    if not providers.available_providers():
        await params.result_callback({
            "unavailable": True,
            "message": "Community search is not configured. Do not guess at what people report.",
        })
        return

    hits = await providers.search(f"{query} site:reddit.com", limit=8)
    anecdotal = [h for h in hits if h.tier is SourceTier.ANECDOTAL]

    kept, corroborated = filter_anecdotes(
        [Anecdote(text=h.text, url=h.url, published=h.published, author=h.author) for h in anecdotal],
        topic,
        now=datetime.now(timezone.utc),
    )
    logger.info(
        f"community search '{query}' -> {len(anecdotal)} raw, {len(kept)} kept, "
        f"corroborated={corroborated}"
    )

    if not kept:
        await params.result_callback({
            "reports": [],
            "corroborated": False,
            "guidance": (
                "No recent, credible first-hand reports were found. Say that you "
                "could not find reliable community reports, rather than implying "
                "none exist or inventing a pattern."
            ),
        })
        return

    await params.result_callback({
        "reports": [{"excerpt": a.text[:400], "url": a.url} for a in kept[:MAX_SPOKEN_HITS]],
        "corroborated": corroborated,
        "guidance": (
            "These are individual accounts, not rules. Describe them as what "
            "some people report. "
            + (
                "Several independent people report this, so you may call it a pattern."
                if corroborated
                else "Too few independent reports to call this a pattern — mention it "
                     "as a single person's experience at most, or not at all."
            )
        ),
    })


_SCHEMAS = [
    FunctionSchema(
        name="search_official_guidance",
        description=(
            "Look up current official US immigration rules, policy, fees, or "
            "processing times from government sources and established immigration "
            "law firms. Use whenever the answer depends on current policy, because "
            "your own knowledge may be out of date."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "A focused search query, e.g. 'H-1B premium processing time 2026'.",
            }
        },
        required=["query"],
    ),
    FunctionSchema(
        name="search_community_experiences",
        description=(
            "Find what applicants report experiencing in practice, for questions "
            "about what actually tends to happen rather than what the rule says. "
            "Results are individual anecdotes and must never be presented as rules."
        ),
        properties={
            "query": {"type": "string", "description": "What experience to look for."},
            "topic": {
                "type": "string",
                "enum": ["processing_times", "policy", "enforcement", "procedure"],
                "description": "Controls how old a report may be before it is discarded.",
            },
        },
        required=["query"],
    ),
]

_HANDLERS = {
    "search_official_guidance": search_official_guidance,
    "search_community_experiences": search_community_experiences,
}


def build_tools() -> tuple[ToolsSchema, dict]:
    """Return the tool schema and handlers to register with the LLM service."""
    if not providers.available_providers():
        # Still registered on purpose: the tools resolve to an "unavailable"
        # message that tells the model to admit it could not check, which is
        # far safer than a model that silently guesses at current policy.
        logger.warning("No search provider keys configured — tools will report unavailable.")
    return ToolsSchema(standard_tools=_SCHEMAS), _HANDLERS
