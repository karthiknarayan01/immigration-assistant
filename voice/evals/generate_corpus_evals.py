"""Generate factual eval cases at scale, with the ground truth taken from sources.

The reason the existing set cannot support confidence is arithmetic: 77 cases
across twelve domains leaves most cells with one to four cases, and 58 of those
cases are graded by a model we have measured to be wrong in both directions.
Multiplying *that* by ten would produce a bigger unreliable number.

So this generator inverts where the trust sits. A model writes the **question**
— that is a writing task, and models are good at it. The **answer** is quoted
from the source text, and the quote is then verified to actually appear in the
chunk before the case is kept. Anything the model invents fails that check and
is discarded, so a hallucinated answer cannot enter the set.

What that buys: every generated case can be graded by matching text, with no
model in the loop. Which is what makes a thousand cases worth having.

The honest limit, stated up front: these are *recall* questions. They test
whether the agent finds the right passage and states the fact in it faithfully.
They do not test judgement, escalation, or whether the advice is any good —
those still need the hand-written suites and a judge. A high score here would
mean "the agent reliably retrieves and states facts in this domain", which is a
real and useful claim, but it is not the whole of "answers well".

Usage:
    PYTHONPATH=. uv run python evals/generate_corpus_evals.py --per-domain 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import re
import sys

from app.llm import get_client

HERE = pathlib.Path(__file__).parent
OUT = HERE / "tasks_generated"

MODEL = "google/gemini-2.5-flash"

#: Concurrency is kept modest so a large batch does not trip provider limits.
CONCURRENCY = 6

INSTRUCTION = """
You are writing US-immigration exam questions from a source text.

Write up to {count} DISTINCT questions that real applicants would ask, each
answered by a different part of the given text. Then quote, for each, the exact
sentence from the text that answers it.

Rules:
- Every question must be answerable from this text alone.
- `answer` states the fact in one short sentence, as a correct answer would.
- `quote` is copied VERBATIM from the text, character for character. It will be
  checked against the source, and any quote that does not appear is discarded.
- Prefer facts with a number, a form number, a deadline or a defined condition,
  because those can be checked exactly. If the text has such facts, use them.
- Do not ask about anything the text does not state. Fewer good questions is
  better than padding.

Return JSON only:
{{"questions": [{{"question": "...", "answer": "...", "quote": "..."}}]}}
""".strip()


def domain_of(chunk: dict) -> str:
    """Bucket a chunk into a visa domain. Coarse on purpose — see the README.

    A precise mapping would need the USCIS subject taxonomy; this reads the
    citation and heading, which is enough to keep generated questions spread
    across the domains users actually ask about.
    """
    # The citation is only section-level ("8 CFR 214.1"), so a paragraph-level
    # rule such as 214.2(f) is invisible in it, and the text carries the detail.
    # But 8 CFR 214.2 alone mentions P-1, H-1B and F-1 alike, so a first match
    # is wrong: a section has to *score* for a domain to belong to it.
    header = " ".join(
        str(chunk.get(field, "")) for field in ("citation", "part", "heading", "about")
    ).lower()
    body = (chunk.get("text") or "")[:1200].lower()
    rules = (
        ("f1_opt", ("214.2(f)", "optional practical training", "f-1 student", "stem opt")),
        ("h1b", ("214.2(h)", "h-1b", "h1b", "specialty occupation", "prevailing wage")),
        ("h4", ("h-4", "h-4 dependent")),
        ("l1", ("214.2(l)", "l-1", "intracompany transferee")),
        ("b1_b2", ("214.2(b)", "b-1", "b-2", "41.31", "visitor for business")),
        ("eb5", ("eb-5", "immigrant investor", "regional center")),
        ("eb1", ("eb-1", "extraordinary ability", "outstanding professor")),
        ("eb2", ("eb-2", "advanced degree", "exceptional ability")),
        ("family", ("204.1", "204.2", "i-130", "immediate relative", "family-sponsored")),
        ("naturalization", ("316.", "312.", "naturaliz", "n-400", "civics")),
        ("asylum", ("208.", "asylum", "withholding of removal")),
    )
    scored: list[tuple[int, str]] = []
    for domain, needles in rules:
        score = 0
        for needle in needles:
            # Word-ish boundaries keep "IH-4" out of the H-4 bucket and "H-1B"
            # out of a rule about H-4 dependants.
            pattern = r"(?<![a-z0-9])" + re.escape(needle.strip()) + r"(?![a-z0-9])"
            score += 3 * len(re.findall(pattern, header))
            score += len(re.findall(pattern, body))
        scored.append((score, domain))

    best_score, best_domain = max(scored)
    # A single passing mention in the body is not enough to claim a domain.
    return best_domain if best_score >= 3 else "general"


#: Tokens that make a fact checkable without a model.
#:
#: An earlier version of this used a bare number regex, which split "P-1" into
#: the token "1" — and a check that passes any answer containing the digit 1
#: verifies nothing while looking like it verifies something. Only tokens with
#: real discriminating power are kept: form numbers, money, multi-digit
#: figures, and durations. A quote with none of those is dropped.
_FORM = re.compile(r"\b(?:Form\s+)?([ING]-\s?\d{2,4}[A-Z]?)\b", re.IGNORECASE)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_FIGURE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\b")
_UNIT = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:business\s+days?|days?|months?|years?|weeks?|hours?|percent|%)\b",
    re.IGNORECASE,
)


def key_tokens(quote: str) -> list[str]:
    """The discriminating content of a quote, or nothing if it has none.

    Deliberately strict. A quote that is all prose — "you must have a job
    offer" — has no token that can be matched without a model, and guessing at
    one would produce a check that passes everything.
    """
    forms = [re.sub(r"\s+", "", m).upper() for m in _FORM.findall(quote)]
    tokens: list[str] = list(forms)
    tokens += [re.sub(r"\s+", "", m) for m in _MONEY.findall(quote)]
    tokens += [m for m in _UNIT.findall(quote)]
    for figure in _FIGURE.findall(quote):
        digits = figure.split(".")[0].replace(",", "")
        # A bare "20" or "101" is a section fragment or a stray count, and a
        # correct answer has no reason to echo it — the first run's most-missed
        # tokens were exactly these. Keep only figures long enough to be a real
        # quantity, and only if they are not part of a form number ("I-20" must
        # not also yield "20").
        # Bare decimals are section references ("204.1", "1241.6"), not
        # quantities — and an answer citing INA 241.6 instead of 8 CFR 1241.6
        # is correct while failing that match. Quantities carry a unit, and
        # _UNIT has already taken those.
        if len(digits) < 4:
            continue
        if any(figure in form for form in forms):
            continue
        tokens.append(figure)
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


class Generator:
    def __init__(self, model: str, per_chunk: int = 5) -> None:
        self.client = get_client()
        self.model = model
        self.per_chunk = per_chunk
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self.attempted = 0
        self.kept = 0
        self.rejected_quote = 0
        self.rejected_uncheckable = 0
        self.failed = 0

    async def one(self, chunk: dict) -> list[dict]:
        """One chunk in, zero or more verified cases out."""
        cases: list[dict] = []
        async with self.semaphore:
            self.attempted += 1
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": INSTRUCTION.format(count=self.per_chunk)},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "citation": chunk.get("citation"),
                                    "heading": chunk.get("heading"),
                                    "text": (chunk.get("text") or "")[:3000],
                                }
                            ),
                        },
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                payload = json.loads((response.choices[0].message.content or "").strip())
            except Exception:  # noqa: BLE001 - a bad generation is not fatal
                self.failed += 1
                return []

        seen: set[str] = set()
        for item in (payload.get("questions") or [])[: self.per_chunk]:
            quote = item.get("quote") or ""
            question = item.get("question") or ""
            answer = item.get("answer") or ""
            if not (question and answer and quote):
                self.failed += 1
                continue
            # The check that makes this trustworthy: the model's quote must
            # really be in the source. An invented answer cannot survive this.
            if normalise(quote) not in normalise(chunk.get("text")):
                self.rejected_quote += 1
                continue
            tokens = key_tokens(quote)
            if not tokens:
                self.rejected_uncheckable += 1
                continue
            fingerprint = normalise(question)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            self.kept += 1
            cases.append(
                {
                    "question": question,
                    "answer": answer,
                    "quote": quote,
                    "must_contain": tokens,
                    "citation": chunk.get("citation", ""),
                    "source": chunk.get("source", ""),
                    "domain": domain_of(chunk),
                    "generated": True,
                }
            )
        return cases


def chunks_by_domain(chunks: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for chunk in chunks:
        buckets.setdefault(domain_of(chunk), []).append(chunk)
    random.seed(42)
    for bucket in buckets.values():
        random.shuffle(bucket)
    return buckets


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-domain", type=int, default=100)
    parser.add_argument("--domains", default="", help="comma-separated subset")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--per-chunk", type=int, default=5, help="questions asked of each source section")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()

    sys.path.insert(0, str(HERE.parent))
    from evals.generate_evals import load_chunks

    chunks = load_chunks()
    buckets = chunks_by_domain(chunks)
    wanted = [d.strip() for d in args.domains.split(",") if d.strip()] or sorted(buckets)

    args.out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}

    for domain in wanted:
        pool = buckets.get(domain, [])
        if not pool:
            print(f"{domain}: no source material")
            continue
        # Oversample: a share of generations are discarded by the quote check.
        # A chunk is a whole section, so several questions can come from one.
        # Thin domains need that: H-4 has two dozen sections, not a hundred.
        take = min(len(pool), max(12, args.per_domain // args.per_chunk + 8))
        generator = Generator(args.model, per_chunk=args.per_chunk)
        results = await asyncio.gather(*(generator.one(c) for c in pool[:take]))
        cases = [case for batch in results for case in batch]
        for index, case in enumerate(cases[: args.per_domain]):
            case["id"] = f"gen-{domain}-{index:04d}"

        path = args.out / f"{domain}.json"
        path.write_text(json.dumps({"domain": domain, "cases": cases[: args.per_domain]}, indent=2))
        summary[domain] = len(cases[: args.per_domain])
        print(
            f"{domain:16} kept {len(cases[: args.per_domain]):>3}/{take:<4} "
            f"(quote-rejected {generator.rejected_quote}, uncheckable {generator.rejected_uncheckable}, "
            f"failed {generator.failed})"
        )

    print("\n" + json.dumps(summary, indent=2))
    print(f"total: {sum(summary.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
