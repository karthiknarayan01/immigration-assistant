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
from app.failures import FailureKind
from app.observability import (
    STAGE_TOOL,
    log_tool_call,
    log_tool_result,
    measure,
)
from app import knowledge
from app.tools import providers
from app.tools.credibility import Anecdote, filter_anecdotes
from app.tools.sources import SEARCH_GROUPS, SourceTier

MAX_SPOKEN_HITS = 4

#: How much of each retrieved page the model gets to see.
#:
#: Was 600, which threw away half of what the providers had already returned
#: and cost real accuracy: asked how many unemployment days STEM OPT allows,
#: the excerpt cut off at "Students authorized fo..." immediately before the
#: number, and the agent supplied a wrong one from memory. The figure is the
#: whole value of an official page, and it is usually not in the first
#: sentence.
#:
#: Four hits at this size is roughly 1.2k tokens of tool result — affordable
#: against a ~2k system prompt, and it arrives after the user is already
#: hearing filler audio.
MAX_EXCERPT_CHARS = 1500


#: Past this, a figure that can change is treated as unverified. Judged
#: against the failure it exists to prevent: a real Federal Register page
#: stating a superseded $460 filing fee was cited as current, because
#: nothing in the payload said how old the page was in terms the model
#: would act on.
STALE_AFTER_DAYS = 365


def _age_note(published) -> str:
    """Say plainly whether a figure from this source can be trusted as current.

    The date alone is not enough. Asked to compare a date against today, the
    model reliably gets the arithmetic right and then uses the number anyway,
    so the judgement is made here and stated as an instruction.
    """
    if published is None:
        return "undated — do not state any fee, processing time or quota from this as current"
    age_days = (datetime.now(timezone.utc) - published).days
    if age_days > STALE_AFTER_DAYS:
        years = age_days / 365
        return (
            f"{years:.1f} years old — treat any fee, processing time or quota "
            "here as possibly superseded; say when it was published and send "
            "the user to the official page"
        )
    return "recent — figures here may be stated as current, with the date"


def _format_official(hits) -> dict:
    results = []
    for hit in hits[:MAX_SPOKEN_HITS]:
        results.append({
            "title": hit.title,
            "url": hit.url,
            "excerpt": hit.text[:MAX_EXCERPT_CHARS],
            "published": hit.published.date().isoformat() if hit.published else "undated",
            "currency": _age_note(hit.published),
            "archived": hit.is_archived,
            # The model is told to treat these tiers differently, so it has to
            # be able to see which is which.
            "trust": hit.tier.value,
        })
    return {"results": results, "count": len(results)}


async def search_official_guidance(params: FunctionCallParams):
    """Authoritative-first search: government sources and the immigration bar."""
    query = str(params.arguments.get("query", "")).strip()
    # The automatic widening below only triggers on an empty result. That
    # misses the more common failure: the allowlist returns something real
    # that does not answer the question — asked for H-1B refusal rates in
    # India it returned nationality-blind I-129 totals — and the model, which
    # can see that, had no way to ask for anything else.
    wider = bool(params.arguments.get("wider", False))
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

    log_tool_call("search_official_guidance", {"query": query, "wider": wider})
    with measure(STAGE_TOOL, "search_official_guidance", query_chars=len(query), wider=wider) as span:
        hits = (
            await providers.search(query, limit=8)
            if wider
            else await providers.search_groups(query, SEARCH_GROUPS, limit=4)
        )
        span["hits"] = len(hits)

    if wider:
        # Everything that is not a forum post, ranked by trust. The model is
        # told to attribute rather than assert, because most of this will not
        # be a government source.
        reported = [h for h in hits if h.tier is not SourceTier.ANECDOTAL][:MAX_SPOKEN_HITS]
        await params.result_callback({
            "results": [
                {
                    "title": h.title,
                    "url": h.url,
                    "excerpt": h.text[:MAX_EXCERPT_CHARS],
                    "published": h.published.date().isoformat() if h.published else "undated",
                    "currency": _age_note(h.published),
                    "trust": h.tier.value,
                }
                for h in reported
            ],
            "count": len(reported),
            "guidance": (
                "Wider search, so most of this is reporting rather than official "
                "guidance. Say who is reporting it and that you could not confirm "
                "it officially. Anything marked authoritative may still be stated "
                "as fact. Giving the person an attributed figure beats telling "
                "them you found nothing."
            ),
        })
        return
    failure = providers.take_last_failure()
    official = [h for h in hits if h.tier in (SourceTier.AUTHORITATIVE, SourceTier.PROFESSIONAL)]
    logger.info(
        f"official search '{query}' -> {len(official)} usable hits, failure={failure}"
    )

    # This tool is load-bearing: without it the agent is answering current
    # policy questions from a training cutoff. A billing failure here has to
    # reach the user, not be quietly absorbed.
    if failure in (FailureKind.FUNDS, FailureKind.AUTH) and not official:
        await params.result_callback({
            "unavailable": True,
            "reason": failure.value,
            "message": (
                "Search is unavailable right now because of an account problem "
                "on our side, so you cannot verify current policy. Tell the user "
                "plainly that you cannot look this up at the moment and that "
                "anything you say from memory may be out of date. Do not guess "
                "at current processing times, fees, or dates."
            ),
        })
        return

    if not official and FALLBACK_WHEN_EMPTY:
        # The allowlist is a list of what we thought of in advance. Questions
        # about what is happening right now — refusal trends, a consulate
        # changing behaviour — are reported outside it or not at all, so
        # giving up here returns "I couldn't find anything" precisely when
        # the person most needs something.
        logger.info(f"official search '{query}' found nothing on-allowlist; widening")
        with measure(STAGE_TOOL, "search_official_fallback", query_chars=len(query)) as span:
            wider = await providers.search(query, limit=6)
            span["hits"] = len(wider)
        reported = [h for h in wider if h.tier is not SourceTier.ANECDOTAL][:MAX_SPOKEN_HITS]
        if reported:
            await params.result_callback({
                "results": [
                    {
                        "title": h.title,
                        "url": h.url,
                        "excerpt": h.text[:MAX_EXCERPT_CHARS],
                        "published": h.published.date().isoformat() if h.published else "undated",
                        "currency": _age_note(h.published),
                        "trust": "reported",
                    }
                    for h in reported
                ],
                "count": len(reported),
                "guidance": (
                    "No official source covered this, so these come from wider "
                    "reporting. Attribute them — say what is being reported and "
                    "by whom — and say you could not confirm it against an "
                    "official source. Do not state any of it as settled fact. "
                    "This is still far more useful than saying you found nothing."
                ),
            })
            return

    if not official:
        await params.result_callback({
            "results": [],
            "count": 0,
            "guidance": (
                "No official source matched. Say you could not find current "
                "official guidance rather than filling the gap from memory."
            ),
        })
        return

    payload = _format_official(official)
    log_tool_result("search_official_guidance", payload)
    await params.result_callback(payload)


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

    # Parallel when configured — it indexes forums far better than the
    # general providers — otherwise an unconstrained search filtered to forum
    # sources afterwards.
    log_tool_call("search_community_experiences", {"query": query, "topic": topic})
    with measure(
        STAGE_TOOL, "search_community_experiences", query_chars=len(query), topic=topic
    ) as span:
        hits = await providers.search_community(query, limit=10)
        span["hits"] = len(hits)
    # Unlike official guidance, this tool is optional: losing it costs colour,
    # not correctness. A billing failure here degrades quietly rather than
    # interrupting the answer with an account problem the user cannot act on.
    failure = providers.take_last_failure()
    if failure:
        logger.warning(f"community search degraded ({failure.value})")

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

    reports = [
        {
            "story": a.text[:700],
            "url": a.url,
            "when": a.published.date().isoformat() if a.published else "date unknown",
        }
        for a in kept[:MAX_SPOKEN_HITS]
    ]
    undated = any(r["when"] == "date unknown" for r in reports)

    await params.result_callback({
        "reports": reports,
        "corroborated": corroborated,
        "guidance": (
            "These are individual accounts, not rules. Retell them as stories — "
            "what this person was going through and how it turned out — rather "
            "than compressing them into a statistic. "
            + (
                "Several independent people report this, so you may call it a pattern. "
                if corroborated
                else "Too few independent reports to call this a pattern — present it "
                     "as one person's experience. "
            )
            + (
                "At least one of these has no date, so say plainly that you do not "
                "know when it was posted and that the rules may have changed since."
                if undated
                else ""
            )
        ),
    })


async def lookup_regulation(params: FunctionCallParams):
    """Read the regulation text itself, from a local copy of the CFR.

    Faster and more complete than web search for the rules that do not
    change: no network call, and the full text of the section rather than
    whichever 1500 characters a search provider chose to return.
    """
    query = str(params.arguments.get("query", "")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    if not knowledge.available():
        await params.result_callback({
            "unavailable": True,
            "message": (
                "The regulation text is not loaded. Use search_official_guidance "
                "instead, and say you could not check the regulation directly."
            ),
        })
        return

    log_tool_call("lookup_regulation", {"query": query})
    with measure(STAGE_TOOL, "lookup_regulation", query_chars=len(query)) as span:
        passages = knowledge.search(query, limit=3)
        span["hits"] = len(passages)

    if not passages:
        await params.result_callback({
            "results": [],
            "count": 0,
            "guidance": (
                "Nothing in the regulations matched. Try search_official_guidance, "
                "which also covers USCIS policy and guidance rather than only the "
                "regulation text."
            ),
        })
        return

    payload = {
        "results": [
            {
                "citation": p.citation,
                "heading": p.heading,
                "text": p.text,
            }
            for p in passages
        ],
        "count": len(passages),
        "as_of": knowledge.AS_OF,
        "guidance": (
            "This is the current regulation text. State it as fact and cite the "
            "section. It does NOT contain fees, processing times or the visa "
            "bulletin — search for those instead, because they change."
        ),
    }
    log_tool_result("lookup_regulation", payload)
    await params.result_callback(payload)


_SCHEMAS = [
    FunctionSchema(
        name="lookup_regulation",
        description=(
            "Read the actual text of US immigration regulations (8 CFR) from a "
            "local copy — instant, no network. Use this FIRST for anything the "
            "regulations settle: grace periods, day counts, unemployment limits, "
            "status conditions, eligibility requirements, change of status rules. "
            "It returns the full section with its citation. It does NOT contain "
            "fees, processing times or the visa bulletin, because those change — "
            "use search_official_guidance for those."
        ),
        properties={
            "query": {
                "type": "string",
                "description": (
                    "What rule to look up, e.g. 'unemployment days allowed on "
                    "post-completion OPT' or 'grace period after H-1B employment ends'."
                ),
            }
        },
        required=["query"],
    ),
    FunctionSchema(
        name="search_official_guidance",
        description=(
            "Look up current official US immigration rules, policy, fees, or "
            "processing times from government sources and established immigration "
            "law firms. Use whenever the answer depends on current policy, because "
            "your own knowledge may be out of date. "
            "REQUIRED before stating any specific number — a grace period, day "
            "count, deadline, filing fee, validity period, threshold, or cap. Do "
            "not answer such a question from memory; remembered figures are "
            "frequently stale and are acted on as though they were checked."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "A focused search query, e.g. 'H-1B premium processing time 2026'.",
            },
            "wider": {
                "type": "boolean",
                "description": (
                    "Search beyond government and law-firm sources. Set this to true "
                    "and call again when the first search returned nothing, or "
                    "returned official pages that do not actually answer what was "
                    "asked — which is common for questions about trends, statistics "
                    "by country, or how something is going lately, since official "
                    "sites rarely publish those. Results must be attributed rather "
                    "than asserted."
                ),
            },
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
    "lookup_regulation": lookup_regulation,
}


def build_tools() -> tuple[ToolsSchema, dict]:
    """Return the tool schema and handlers to register with the LLM service."""
    if not providers.available_providers():
        # Still registered on purpose: the tools resolve to an "unavailable"
        # message that tells the model to admit it could not check, which is
        # far safer than a model that silently guesses at current policy.
        logger.warning("No search provider keys configured — tools will report unavailable.")
    return ToolsSchema(standard_tools=_SCHEMAS), _HANDLERS
