"""Generate eval cases from the source material, using a judge model.

Eval-driven development: rather than hand-writing every question, a judge model
reads the policy documents and writes the smallest possible "atomic" questions
— one fact or one condition per question — with a grading rubric grounded in
that text. The larger questions users actually ask are then built as *combos*
of two to four atomic questions, and the judge writes a rubric for the combo by
combining the atomics' rubrics.

Why smallest-first: a question that mixes three facts can be right on two and
wrong on one, and a single 0-3 score hides which. Atomic questions make each
fact separately checkable; combos verify the agent can hold several facts at
once, which is what a real multi-part question demands.

The fact questions come from the local 8 CFR pack (app/knowledge). Recent-
development and reasoning questions are framed against the same idea — small,
separately-checkable assertions — but the source material is live search
rather than static text, and the harness also verifies the tool sequence.

Usage:
    PYTHONPATH=. uv run python evals/generate_evals.py [--chunks N] [--combos M] [--out PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random

import yaml

from app import knowledge
from app.config import settings
from app.llm import get_client

HERE = pathlib.Path(__file__).resolve().parent
PACK = pathlib.Path(knowledge.__file__).resolve().parent / "cfr.json"

MODEL = settings.judge_model


def load_chunks() -> list[dict]:
    if not PACK.exists():
        raise SystemExit(f"no regulation pack at {PACK}; run scripts/build_knowledge_pack.py")
    return json.loads(PACK.read_text()).get("chunks", [])


ATOMIC_INSTRUCTION = """
You are writing US-immigration eval questions, one atom at a time.

Read the given 8 CFR text and write ONE question that tests a single, smallest
possible fact or condition in it. The question must be answerable from this
text alone, and phrased the way a real applicant would ask it.

For that one question, write a grading rubric:

- requires: 1-2 things a full-credit answer must state, taken from the text
- forbids:  1-2 specific errors that make an answer wrong

Return JSON only:
{"question": "...", "requires": ["..."], "forbids": ["..."], "citation": "..."}

Keep the question about ONE fact. If the text covers several facts, pick one.
""".strip()


COMBINE_INSTRUCTION = """
You are combining US-immigration eval questions into one realistic multi-part
question, the way a real applicant would bundle several concerns together.

Given a list of atomic questions (each with its own requires/forbids), write:

- question: one natural question that bundles the given atoms (do not add facts
  outside them)
- requires: the union of the atoms' requirements, deduplicated
- forbids:  the union of the atoms' forbids, deduplicated

Return JSON only:
{"question": "...", "requires": ["..."], "forbids": ["..."]}
""".strip()


async def _json_call(system: str, user: str) -> dict:
    client = get_client()
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads((response.choices[0].message.content or "").strip())
    except json.JSONDecodeError:
        return {}


async def make_atomic(chunk: dict) -> dict:
    user = json.dumps({"citation": chunk.get("citation"), "heading": chunk.get("heading"), "text": chunk.get("text")[:2500]})
    return await _json_call(ATOMIC_INSTRUCTION, user)


async def combine(atoms: list[dict]) -> dict:
    user = json.dumps(atoms, indent=2)
    return await _json_call(COMBINE_INSTRUCTION, user)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=20, help="number of CFR chunks to turn into atomics")
    parser.add_argument("--combos", type=int, default=8, help="number of combo questions to build")
    parser.add_argument("--out", type=pathlib.Path, default=HERE / "tasks" / "official_answer" / "generated.yaml")
    args = parser.parse_args()

    chunks = load_chunks()
    random.seed(42)
    sample = random.sample(chunks, min(args.chunks, len(chunks)))

    atomics = []
    for index, chunk in enumerate(sample, start=1):
        atomic = await make_atomic(chunk)
        if atomic.get("question"):
            atomic["atomic_of"] = chunk.get("citation")
            atomics.append(atomic)
        print(f"[{index:>2}/{len(sample)}] {chunk.get('citation')}: {atomic.get('question', '(skipped)')[:60]}")

    combos = []
    for index in range(args.combos):
        group = random.sample(atomics, k=min(random.choice([2, 3, 4]), len(atomics)))
        combo = await combine(group)
        if combo.get("question"):
            combos.append(combo)
        print(f"[combo {index + 1}/{args.combos}] {combo.get('question', '(skipped)')[:70]}")

    cases = []
    for i, a in enumerate(atomics, start=1):
        cases.append({
            "id": f"gen-atomic-{i:02d}",
            "category": "factual",
            "question": a["question"],
            "requires": a.get("requires", []),
            "forbids": a.get("forbids", []),
            "source_set": "generated",
            "citation": a.get("citation", ""),
        })
    for i, c in enumerate(combos, start=1):
        cases.append({
            "id": f"gen-combo-{i:02d}",
            "category": "factual",
            "question": c["question"],
            "requires": c.get("requires", []),
            "forbids": c.get("forbids", []),
            "source_set": "generated",
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(cases, sort_keys=False, width=88, allow_unicode=True))
    print(f"\nwrote {len(cases)} generated cases -> {args.out}")
    print("Review them, then merge the good ones into cases.yaml (and delete the rest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
