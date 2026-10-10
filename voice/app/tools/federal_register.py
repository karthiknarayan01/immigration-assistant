"""The Federal Register: official rulemaking, free and structured.

This is the authoritative half of "what changed recently". Every federal rule,
proposed rule, notice and presidential document is published here, and the
agency exposes it as a JSON API with no key.

Why it matters more than a web search for this job: the **document type is
the in-force-vs-proposed distinction**. A `RULE` is in force; a `PRORULE` is
proposed; a `NOTICE` is an announcement; a `PRESDOCU` is an executive order or
proclamation. That is exactly what the recent-developments function has to get
right, and here it arrives as native metadata rather than something the model
has to infer from prose. The API also returns the effective date and the
comment deadline, so the answer can say precisely when something takes effect.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from loguru import logger

FR_DOCUMENTS = "https://www.federalregister.gov/api/v1/documents.json"

_TIMEOUT = httpx.Timeout(20.0)

#: Document types, mapped to the plain reading the model is given. The mapping
#: is applied here rather than left to the model: whether something is in force
#: is the single most important fact in a developments answer, and it is known
#: exactly, so it should never be inferred.
#: Query aliases -> the API's own document-type codes.
DOCUMENT_TYPES = {
    "final_rule": "RULE",
    "proposed_rule": "PRORULE",
    "notice": "NOTICE",
    "presidential_document": "PRESDOCU",
}


def status_of(document_type: str) -> str:
    """Plain reading of a document type.

    The API returns human names ("Proposed Rule", "Rule"), not its codes, so
    the mapping is keyword-based and ordered: "proposed" is checked before
    "rule", because "Proposed Rule" contains both.
    """
    text = (document_type or "").lower()
    if "proposed" in text:
        return "proposed rule — NOT yet in force"
    if "presidential" in text or "executive order" in text or "proclamation" in text:
        return "presidential document — executive order or proclamation"
    if "notice" in text:
        return "notice — an announcement, not a rule"
    if "rule" in text:
        return "final rule — in force"
    return document_type or "unknown"

#: The agencies whose documents bear on US immigration. Slugs are the Federal
#: Register's own; callers pass the short alias so the model never has to know
#: a slug.
#: Slugs verified against the live API. USCIS and EOIR publish separately from
#: their parent departments, so filtering on `dhs` alone misses every USCIS
#: notice — which is most of what a visa question cares about.
AGENCIES = {
    "dhs": ("homeland-security-department", "DHS (department-wide)"),
    "uscis": ("u-s-citizenship-and-immigration-services", "USCIS"),
    "ice": ("u-s-immigration-and-customs-enforcement", "ICE"),
    "state": ("state-department", "State Department (visas, consular)"),
    "labor": ("labor-department", "Labor Department"),
    "eta": (
        "employment-and-training-administration",
        "DOL Employment and Training Administration (PERM, LCA)",
    ),
    "eoir": ("executive-office-for-immigration-review", "EOIR (immigration courts)"),
}

_FIELDS = (
    "title",
    "type",
    "abstract",
    "publication_date",
    "effective_on",
    "comments_close_on",
    "citation",
    "html_url",
    "agencies",
    "document_number",
)


@dataclass
class FrDocument:
    title: str
    doc_type: str
    type_meaning: str
    abstract: str
    publication_date: str
    effective_on: str | None
    comments_close_on: str | None
    citation: str
    url: str
    agencies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "document_type": self.doc_type,
            "status": self.type_meaning,
            "abstract": self.abstract,
            "published": self.publication_date,
            "effective_on": self.effective_on or "",
            "comments_close_on": self.comments_close_on or "",
            "citation": self.citation,
            "url": self.url,
            "agencies": self.agencies,
        }


def _agency_names(items) -> list[str]:
    names = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return names


async def search(
    query: str,
    *,
    document_type: str = "any",
    agency: str = "any",
    since: str | None = None,
    limit: int = 8,
) -> list[FrDocument]:
    """Search the Federal Register for rules, proposals and notices.

    `since` is an ISO date (YYYY-MM-DD). `document_type` and `agency` are the
    aliases from DOCUMENT_TYPES / AGENCIES, or "any".
    """
    params: list[tuple[str, str]] = [
        ("conditions[term]", query),
        ("per_page", str(limit)),
        ("order", "newest"),
    ]
    if document_type != "any" and document_type in DOCUMENT_TYPES:
        params.append(("conditions[type][]", DOCUMENT_TYPES[document_type]))
    if agency != "any" and agency in AGENCIES:
        params.append(("conditions[agencies][]", AGENCIES[agency][0]))
    if since:
        params.append(("conditions[publication_date][gte]", since))
    params.extend(("fields[]", name) for name in _FIELDS)

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"Accept": "application/json"}) as client:
        response = await client.get(FR_DOCUMENTS, params=params)
        response.raise_for_status()
        payload = response.json()

    documents = []
    for item in payload.get("results", []):
        doc_type = item.get("type", "")
        documents.append(
            FrDocument(
                title=item.get("title") or "",
                doc_type=doc_type,
                type_meaning=status_of(doc_type),
                abstract=(item.get("abstract") or "")[:1200],
                publication_date=item.get("publication_date") or "",
                effective_on=item.get("effective_on"),
                comments_close_on=item.get("comments_close_on"),
                citation=item.get("citation") or "",
                url=item.get("html_url") or "",
                agencies=_agency_names(item.get("agencies")),
            )
        )

    logger.info(
        f"federal register '{query[:50]}' -> {len(documents)} documents "
        f"(type={document_type}, agency={agency})"
    )
    return documents
