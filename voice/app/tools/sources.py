"""Source tiering.

The product's whole value is separating what the law says from what people
report happening. That only works if every retrieved fact carries a known
trust level, so classification happens here rather than being left to the
model's judgement.
"""

from enum import Enum
from urllib.parse import urlparse


class SourceTier(str, Enum):
    #: Government and primary law. May be stated as fact.
    AUTHORITATIVE = "authoritative"
    #: Immigration bar, established firms, reputable nonprofits. Attribute it.
    PROFESSIONAL = "professional"
    #: Forums and social media. Never stated as fact — only "people report".
    ANECDOTAL = "anecdotal"
    #: Anything unrecognised. Treated as anecdotal, never better.
    UNKNOWN = "unknown"


AUTHORITATIVE_DOMAINS = frozenset({
    "uscis.gov",
    "travel.state.gov",
    "state.gov",
    "ecfr.gov",
    "federalregister.gov",
    "justice.gov",          # EOIR / BIA decisions
    "dhs.gov",
    "cbp.gov",
    "ice.gov",
    "dol.gov",              # PERM / LCA
    "law.cornell.edu",      # INA / USC
    "govinfo.gov",
    "congress.gov",
})

PROFESSIONAL_DOMAINS = frozenset({
    "aila.org",
    "murthy.com",
    "fragomen.com",
    "bal.com",
    "cyrusmehta.com",
    "boundless.com",
    "nolo.com",
    "migrationpolicy.org",
    "americanimmigrationcouncil.org",
    "immigrationimpact.com",
    "nafsa.org",
})

ANECDOTAL_DOMAINS = frozenset({
    "reddit.com",
    "x.com",
    "twitter.com",
    "quora.com",
    "trackitt.com",
    "visajourney.com",
    "immihelp.com",
    "youtube.com",
    "facebook.com",
})


def _registrable(host: str) -> str:
    """Reduce a hostname to something matchable against the domain lists."""
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    # travel.state.gov must not collapse to state.gov, so try longest first.
    for size in range(len(parts), 1, -1):
        candidate = ".".join(parts[-size:])
        if (
            candidate in AUTHORITATIVE_DOMAINS
            or candidate in PROFESSIONAL_DOMAINS
            or candidate in ANECDOTAL_DOMAINS
        ):
            return candidate
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify(url: str) -> SourceTier:
    """Map a URL to its trust tier."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return SourceTier.UNKNOWN
    if not host:
        return SourceTier.UNKNOWN

    domain = _registrable(host)
    if domain in AUTHORITATIVE_DOMAINS:
        return SourceTier.AUTHORITATIVE
    if domain in PROFESSIONAL_DOMAINS:
        return SourceTier.PROFESSIONAL
    if domain in ANECDOTAL_DOMAINS:
        return SourceTier.ANECDOTAL
    # An unrecognised blog is not trustworthy just because it isn't Reddit.
    return SourceTier.UNKNOWN


#: Passed to search providers so official queries stay on primary sources.
OFFICIAL_SEARCH_DOMAINS = sorted(AUTHORITATIVE_DOMAINS | PROFESSIONAL_DOMAINS)

#: Subreddits worth listening to. Everything else is dropped before scoring.
COMMUNITY_SUBREDDITS = (
    "immigration",
    "USCIS",
    "h1b",
    "f1visa",
    "immigrationlaw",
    "greencarddiary",
    "EB2_NIW",
    "AskImmigration",
)
