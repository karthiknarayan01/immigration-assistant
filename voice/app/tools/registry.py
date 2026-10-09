"""Tools exposed to the model.

Deliberately few. Every extra tool is another thing the model can pick wrongly,
and every tool call costs seconds the user is waiting. The deep-research,
academic, and YouTube tools are intentionally absent: they add 10-30s without
being load-bearing for immigration questions.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from app import knowledge
from app.config import settings
from app.failures import FailureKind
from app.observability import (
    STAGE_TOOL,
    log_tool_call,
    log_tool_result,
    measure,
)
from app.tools import federal_register, providers, x_search
from app.tools.credibility import Anecdote, filter_anecdotes
from app.tools.schema import FunctionSchema, ToolCallParams, ToolsSchema
from app.tools.sources import PROFESSIONAL_GROUP, SEARCH_GROUPS, SourceTier

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


async def _search_web(query: str) -> dict:
    """General web search — no allowlist — for when official sources miss.

    Runs the same Tavily/Exa providers without a domain constraint. Results are
    returned as a normal results list (with trust tiers) rather than prose, so
    the model reads them through the same "attributed, not asserted" path as
    anything else from a non-official source.
    """
    hits = await providers.search(query, limit=6)
    if not hits:
        return {
            "results": [],
            "count": 0,
            "guidance": (
                "Even a wide web search found nothing usable. Say so plainly "
                "rather than answering from memory."
            ),
        }
    results = [
        {
            "title": hit.title,
            "url": hit.url,
            "excerpt": hit.text[:MAX_EXCERPT_CHARS],
            "published": hit.published.date().isoformat() if hit.published else "undated",
            "currency": _age_note(hit.published),
            "trust": hit.tier.value,
        }
        for hit in hits[:6]
    ]
    return {
        "results": results,
        "count": len(results),
        "guidance": (
            "This came from a general web search rather than the official "
            "allowlist. Say what is being reported and by whom, give the dates "
            "it carries, and say you could not confirm it against an official "
            "source. An attributed figure beats telling the person you found "
            "nothing."
        ),
    }


async def search_official_guidance(params: ToolCallParams):
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

    # A missing or exhausted search subscription must not block the wider path.
    if not wider and not providers.available_providers():
        logger.info("no search providers configured; using the wider web search instead")
        wider = True

    log_tool_call("search_official_guidance", {"query": query, "wider": wider})
    if not wider:
        with measure(STAGE_TOOL, "search_official_guidance", query_chars=len(query)) as span:
            hits = await providers.search_groups(query, SEARCH_GROUPS, limit=4)
            span["hits"] = len(hits)
    else:
        hits = []

    if wider:
        # General web rather than the allowlist: the questions people actually
        # ask are about what is happening now, and that reporting is rarely on
        # a .gov domain.
        payload = await _search_web(query)
        await params.result_callback(payload)
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
        # The allowlist is a list of what we thought of in advance, and the
        # questions people ask are about what is happening now. Fall through
        # to a general web search rather than returning nothing.
        logger.info(f"official search '{query}' found nothing on-allowlist; widening to web")
        payload = await _search_web(query)
        if payload.get("count"):
            await params.result_callback(payload)
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


async def search_community_experiences(params: ToolCallParams):
    """What people report in practice — filtered, and never stated as fact."""
    query = str(params.arguments.get("query", "")).strip()
    topic = str(params.arguments.get("topic", "policy")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    # X alone is enough to answer from, so a missing forum provider is not a
    # dead end when Grok is configured.
    if not providers.available_providers() and not x_search.available():
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
        # Forums and X in parallel. X is where a change in practice shows up
        # first; forums carry the longer, more detailed accounts. Neither is
        # trusted more than the other — both land in the same filter.
        hits, posts = await asyncio.gather(
            providers.search_community(query, limit=10),
            x_search.search(query),
        )
        span["hits"] = len(hits)
        span["x_posts"] = len(posts)
    # Unlike official guidance, this tool is optional: losing it costs colour,
    # not correctness. A billing failure here degrades quietly rather than
    # interrupting the answer with an account problem the user cannot act on.
    failure = providers.take_last_failure()
    if failure:
        logger.warning(f"community search degraded ({failure.value})")

    anecdotal = [h for h in hits if h.tier is SourceTier.ANECDOTAL]

    # X posts are appended as peers, not as context. Twenty posts saying the
    # same thing is usually one claim and nineteen quote-tweets, so they face
    # the same corroboration gate — three independent authors — as anything
    # else here.
    kept, corroborated = filter_anecdotes(
        [Anecdote(text=h.text, url=h.url, published=h.published, author=h.author) for h in anecdotal]
        + posts,
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


async def lookup_policy(params: ToolCallParams):
    """Read authoritative source text, from a local corpus.

    Covers the rules that do not change — 8 CFR (DHS), 22 CFR (State
    Department visas), 20 CFR (Labor/PERM) and the State Department's 9 FAM
    consular guidance — with no network call, and the full text of the section
    rather than whichever 1500 characters a search provider chose to return.

    Consular practice lives only in 9 FAM, which is why the corpus is not
    regulations alone: a question about a consulate cannot be answered from the
    CFR.
    """
    query = str(params.arguments.get("query", "")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    if not knowledge.available():
        await params.result_callback({
            "unavailable": True,
            "message": (
                "The policy corpus is not loaded. Use search_official_guidance "
                "instead, and say you could not check the source text directly."
            ),
        })
        return

    log_tool_call("lookup_policy", {"query": query})
    with measure(STAGE_TOOL, "lookup_policy", query_chars=len(query)) as span:
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
    log_tool_result("lookup_policy", payload)
    await params.result_callback(payload)


async def search_recent_developments(params: ToolCallParams):
    """The reporting half of "what changed": press and practitioner comment.

    Companion to `search_federal_register`, not a duplicate of it. The Federal
    Register says what is officially proposed or in force; this says how a
    change is being reported and what immigration lawyers are saying it means
    in practice — which is where the practical reading of a rule shows up
    first, and often long before it is settled.

    Both halves are returned in one payload, clearly separated, because an
    answer that reads a proposal as settled law is the failure this function
    exists to prevent.
    """
    query = str(params.arguments.get("query", "")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    if not providers.available_providers():
        await params.result_callback({
            "unavailable": True,
            "reason": "no_search_provider",
            "message": (
                "No search provider is configured, so you cannot check recent "
                "developments. Say so plainly, and do not describe recent "
                "changes from memory — your knowledge has a cutoff and recent "
                "immigration changes are exactly what it will be wrong about."
            ),
        })
        return

    log_tool_call("search_recent_developments", {"query": query})
    with measure(STAGE_TOOL, "search_recent_developments", query_chars=len(query)) as span:
        # Press and the immigration bar in parallel: one says what happened,
        # the other says what it means. Recency is enforced inside search_news,
        # because a well-ranked page from three years ago is not a development.
        news, commentary = await asyncio.gather(
            providers.search_news(query, days=45, limit=6),
            providers.search(query, domains=list(PROFESSIONAL_GROUP), limit=4),
        )
        span["news_hits"] = len(news)
        span["commentary_hits"] = len(commentary)

    failure = providers.take_last_failure()
    if failure:
        logger.warning(f"developments search degraded ({failure.value})")

    def _item(hit) -> dict:
        return {
            "title": hit.title,
            "url": hit.url,
            "excerpt": hit.text[:MAX_EXCERPT_CHARS],
            "published": hit.published.date().isoformat() if hit.published else "undated",
            "currency": _age_note(hit.published),
            "trust": hit.tier.value,
        }

    if not news and not commentary:
        await params.result_callback({
            "reported_developments": [],
            "practitioner_commentary": [],
            "count": 0,
            "guidance": (
                "Nothing recent was found. Say you could not find recent "
                "reporting rather than describing changes from memory, and "
                "suggest checking the Federal Register for official actions."
            ),
        })
        return

    payload = {
        "reported_developments": [_item(hit) for hit in news],
        "practitioner_commentary": [_item(hit) for hit in commentary],
        "count": len(news) + len(commentary),
        "guidance": (
            "These are REPORTED developments, not official rulemaking. For what "
            "is actually proposed or in force, check search_federal_register. "
            "Say who reported each item and give its date. Distinguish a proposal "
            "or announcement from something in effect, and press reporting from "
            "an official statement. Practitioner commentary is attributed "
            "opinion, not law."
        ),
    }
    log_tool_result("search_recent_developments", payload)
    await params.result_callback(payload)


async def search_federal_register(params: ToolCallParams):
    """Official rulemaking: what is proposed, in force, or announced.

    Free, no key, and structured. It is the only source here that states
    outright whether a change is a final rule (in force), a proposed rule (not
    in force), a notice, or a presidential document — and it carries the
    effective date and comment deadline. That distinction is the whole point:
    a development answer that lets a proposal sound like current law is the
    failure this tool exists to prevent.
    """
    query = str(params.arguments.get("query", "")).strip()
    if not query:
        await params.result_callback({"error": "No query provided."})
        return

    document_type = str(params.arguments.get("document_type", "any")).strip() or "any"
    agency = str(params.arguments.get("agency", "any")).strip() or "any"
    try:
        since_days = int(params.arguments.get("since_days") or 365)
    except (TypeError, ValueError):
        since_days = 365
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()

    log_tool_call(
        "search_federal_register",
        {"query": query, "document_type": document_type, "agency": agency, "since_days": since_days},
    )
    with measure(STAGE_TOOL, "search_federal_register", query_chars=len(query)) as span:
        documents = await federal_register.search(
            query, document_type=document_type, agency=agency, since=since, limit=8
        )
        span["hits"] = len(documents)

    if not documents:
        await params.result_callback({
            "results": [],
            "count": 0,
            "guidance": (
                f"No Federal Register documents matched since {since}. That does "
                "not mean nothing changed — check press reporting with "
                "search_recent_developments, and say you could not find an "
                "official document rather than guessing."
            ),
        })
        return

    payload = {
        "results": [document.as_dict() for document in documents],
        "count": len(documents),
        "guidance": (
            "This is official rulemaking. 'status' states whether each document "
            "is in force (final rule), proposed (NOT yet in force), a notice, or "
            "a presidential document — say which, and never let a proposed rule "
            "sound like current law. Give the publication date and, when there "
            "is one, the effective date. Cite the Federal Register citation."
        ),
    }
    log_tool_result("search_federal_register", payload)
    await params.result_callback(payload)


_SCHEMAS = [
    FunctionSchema(
        name="lookup_policy",
        description=(
            "Read authoritative US immigration source text from a local corpus — "
            "instant, no network. It covers 8 CFR (DHS rules: status, grace "
            "periods, work authorisation, adjustment), 22 CFR (State Department "
            "visa regulations: refusal grounds, issuance), 20 CFR (Labor: PERM) "
            "and the State Department's 9 FAM consular guidance (what actually "
            "happens at an embassy: interviews, 221(g), refusals). "
            "Use this FIRST for anything these sources settle. It returns the "
            "full passage with its citation. It does NOT contain fees, "
            "processing times or the visa bulletin, because those change — use "
            "search_official_guidance for those."
        ),
        properties={
            "query": {
                "type": "string",
                "description": (
                    "What to look up, e.g. 'unemployment days allowed on "
                    "post-completion OPT', 'grace period after H-1B employment "
                    "ends', or '221(g) administrative processing'."
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
    FunctionSchema(
        name="search_recent_developments",
        description=(
            "Find how recent US immigration developments are being REPORTED: "
            "press coverage and what immigration lawyers are saying about them. "
            "Use this alongside search_federal_register when a question is about "
            "what has changed lately — the Federal Register says what is official, "
            "this says what it means in practice and what people are saying. "
            "Results are reported, not official, and must be attributed."
        ),
        properties={
            "query": {
                "type": "string",
                "description": (
                    "A focused query about the recent development, e.g. 'new H-1B "
                    "rule' or 'latest executive order on visas'."
                ),
            }
        },
        required=["query"],
    ),
    FunctionSchema(
        name="search_federal_register",
        description=(
            "Search the Federal Register for official US immigration rulemaking: "
            "final rules (in force), proposed rules (NOT yet in force), notices, "
            "and presidential documents such as executive orders. Free and "
            "authoritative. Use this FIRST for any question about what has "
            "recently changed, been proposed, or been announced, because each "
            "result states outright whether it is in force — then use "
            "search_recent_developments for how it is being reported."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "What to look for, e.g. 'H-1B grace period' or 'OPT fees'.",
            },
            "document_type": {
                "type": "string",
                "enum": ["any", "final_rule", "proposed_rule", "notice", "presidential_document"],
                "description": (
                    "Restrict to one kind of document. Use 'proposed_rule' to find "
                    "what is not yet in force, 'final_rule' for what is."
                ),
            },
            "agency": {
                "type": "string",
                "enum": ["any", "uscis", "dhs", "ice", "state", "labor", "eta", "eoir"],
                "description": (
                    "Restrict to the agency that issued it. Note USCIS and EOIR "
                    "publish separately from their parent departments."
                ),
            },
            "since_days": {
                "type": "integer",
                "description": "How far back to look, in days. Default 365.",
            },
        },
        required=["query"],
    ),
]

_HANDLERS = {
    "search_official_guidance": search_official_guidance,
    "search_community_experiences": search_community_experiences,
    "search_federal_register": search_federal_register,
    "lookup_policy": lookup_policy,
    "search_recent_developments": search_recent_developments,
}


def build_tools() -> tuple[ToolsSchema, dict]:
    """Return the tool schema and handlers to register with the LLM service."""
    if not providers.available_providers():
        # Still registered on purpose: the tools resolve to an "unavailable"
        # message that tells the model to admit it could not check, which is
        # far safer than a model that silently guesses at current policy.
        logger.warning("No search provider keys configured — tools will report unavailable.")
    return ToolsSchema(standard_tools=_SCHEMAS), _HANDLERS
