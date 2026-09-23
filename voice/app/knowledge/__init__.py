"""Local regulation lookup, from the pack built by scripts/build_knowledge_pack.py.

Web search gets the agent to the right *page* and then hands it 1500
characters that often stop just before the number. This holds the actual
regulation text, so the sentence that states the rule is available in full,
with a citation the agent can read out.

It is also fast in a way search cannot be: no network, so a lookup is a few
milliseconds against ~2 seconds. For the questions it covers — the rules that
barely move — that removes the tool call from the critical path entirely.

Deliberately keyword scored rather than embedded. The queries are short and
full of distinctive terms (form numbers, "unemployment", "grace period"), the
corpus is 562 chunks, and an embedding model here would add a network call to
the one path whose whole point is not making one.

What is NOT in here: fees, processing times, the visa bulletin. Those change,
and a local copy of a changing number is a stale number with a citation
attached — the exact failure this is meant to prevent.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from collections import Counter
from dataclasses import dataclass

from loguru import logger

PACK = pathlib.Path(__file__).resolve().parent / "cfr.json"

#: Words too common in this corpus to carry signal — every chunk is about
#: aliens, status and the Secretary.
_STOPWORDS = frozenset("""
a an and are as at be by for from has have i if in is it its me my not of on
or that the this to was were what when where which who will with you your
alien aliens status section paragraph secretary united states shall may must
""".split())

_WORD = re.compile(r"[a-z0-9-]+")


@dataclass
class Passage:
    citation: str
    heading: str
    text: str
    score: float


#: Nobody asks a question in the language the CFR is written in. The
#: regulations never say "green card", "cap-gap" or "work permit", so a
#: question using those words scores against the wrong sections entirely —
#: "green card revocation" returned Border Crossing Card rules because
#: "revocation" was the only term that matched anything.
#:
#: Expansion is additive: the user's words are kept as well, so a query that
#: happens to use the regulatory term is not made worse.
_SYNONYMS = {
    "green": ["lawful", "permanent", "resident"],
    "greencard": ["lawful", "permanent", "resident"],
    "card": ["resident", "residence"],
    "gc": ["lawful", "permanent", "resident"],
    "ead": ["employment", "authorization", "document"],
    "opt": ["practical", "training"],
    "revoked": ["revocation", "rescission", "terminate", "termination"],
    "revocation": ["rescission", "terminate", "termination"],
    "deported": ["removal", "deportation"],
    "fired": ["termination", "cessation", "employment"],
    "laid": ["termination", "cessation"],
    "layoff": ["termination", "cessation", "employment"],
    "permit": ["authorization", "document"],
    "sponsor": ["petitioner", "petition"],
    "spouse": ["dependent", "derivative"],
}


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2]


def _expand(terms: list[str]) -> list[str]:
    """Add the regulatory vocabulary for everyday words, keeping both."""
    expanded = list(terms)
    for term in terms:
        expanded.extend(_SYNONYMS.get(term, ()))
    return expanded


#: The CFR is full of sections that look topically perfect and apply to almost
#: nobody: parole rules written for one nationality, benefits under a named
#: Act, superseded provisions kept for reference. Plain BM25 ranks these
#: highly because they use exactly the vocabulary of the question — asked
#: about advance parole it returned a Haiti-specific provision, and asked
#: about green card revocation it returned Border Crossing Card rules.
#:
#: They are demoted rather than removed: someone really asking about the
#: Haitian Refugee Immigration Fairness Act should still find it, which is why
#: the penalty lifts when the query names the thing.
_NARROW_HEADING = re.compile(
    r"\b(libyan|haitian|cuban|nicaraguan|syrian|vietnamese|canada or mexico|"
    r"usmca|trafficking|victims?|former regulations|"
    r"[A-Z][a-z]+ (?:Refugee|Adjustment|Fairness|Relief) Act)\b",
    re.I,
)

#: How much a narrow section is held back when the query does not name it.
_NARROW_PENALTY = 0.3

#: A term in the section heading says what the section is *about*, which is a
#: far stronger signal than the same term buried in its body.
_HEADING_WEIGHT = 3


class _Index:
    """Tiny BM25-ish index, built once at import."""

    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.headings = [c.get("heading", "") for c in chunks]
        self.narrow = [bool(_NARROW_HEADING.search(h)) for h in self.headings]
        self.tokens = [
            Counter(_terms(c["text"]) + _terms(c.get("heading", "")) * _HEADING_WEIGHT)
            for c in chunks
        ]
        self.lengths = [sum(t.values()) or 1 for t in self.tokens]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 1
        document_frequency: Counter = Counter()
        for counts in self.tokens:
            document_frequency.update(counts.keys())
        total = len(chunks) or 1
        self.idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    def search(self, query: str, limit: int) -> list[Passage]:
        asked = _terms(query)
        if not asked:
            return []
        wanted = _expand(asked)

        # Adjacent query words as phrases. "advance parole" and "conditional
        # resident" mean something the two words separately do not, and single
        # terms alone are what let unrelated sections score well. Built from
        # the user's own words, not the expansions, which are not adjacent to
        # anything.
        phrases = [f"{a} {b}" for a, b in zip(asked, asked[1:])]
        lowered_query = query.lower()

        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for index, counts in enumerate(self.tokens):
            score = 0.0
            for term in wanted:
                frequency = counts.get(term)
                if not frequency:
                    continue
                norm = 1 - b + b * self.lengths[index] / self.avg_length
                score += self.idf.get(term, 0.0) * frequency * (k1 + 1) / (frequency + k1 * norm)
            if score <= 0:
                continue

            haystack = (self.chunks[index]["text"] + " " + self.headings[index]).lower()
            for phrase in phrases:
                if phrase in haystack:
                    score *= 1.35

            # A section written for one population is the right answer only
            # when it was asked for. Checked against the query text, so
            # "Haitian adjustment" still finds the Haitian provision.
            if self.narrow[index]:
                match = _NARROW_HEADING.search(self.headings[index])
                if match and match.group(0).lower() not in lowered_query:
                    score *= _NARROW_PENALTY

            scored.append((score, index))

        scored.sort(reverse=True)
        return [
            Passage(
                citation=self.chunks[i]["citation"],
                heading=self.chunks[i].get("heading", ""),
                text=self.chunks[i]["text"],
                score=round(s, 2),
            )
            for s, i in scored[:limit]
        ]


def _load() -> tuple[_Index | None, str]:
    if not PACK.exists():
        logger.warning(
            f"no regulation pack at {PACK}; run scripts/build_knowledge_pack.py. "
            "The agent will fall back to web search for regulation text."
        )
        return None, ""
    data = json.loads(PACK.read_text())
    chunks = data.get("chunks", [])
    logger.info(f"loaded {len(chunks)} regulation chunks (eCFR as of {data.get('as_of')})")
    return _Index(chunks), data.get("as_of", "")


_INDEX, AS_OF = _load()


def available() -> bool:
    return _INDEX is not None


def search(query: str, *, limit: int = 3) -> list[Passage]:
    """Find the regulation passages that best match a question."""
    if _INDEX is None:
        return []
    return _INDEX.search(query, limit)
