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


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2]


class _Index:
    """Tiny BM25-ish index, built once at import."""

    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.tokens = [Counter(_terms(c["text"] + " " + c.get("heading", ""))) for c in chunks]
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
        wanted = _terms(query)
        if not wanted:
            return []
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
            if score > 0:
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
