"""Probe: does the system prompt make the model use its tools?

Two failure modes are silent and expensive, and both are prompt-shaped rather
than model-shaped:

  * the prompt is so long that the model answers from memory instead of
    looking anything up, and
  * a rule that only covers *numbers* ("never state a figure you have not
    looked up") leaves every non-numeric fact to memory — eligibility rules,
    form requirements, definitions.

Run against a candidate wording before trusting it:

    PYTHONPATH=. uv run python scripts/probe_tool_use.py

Set PROBE_MODEL to compare models; set PROBE_CANDIDATE to test a replacement
rule without editing the prompts.
"""

from __future__ import annotations

import asyncio
import os

from app.llm import build_tools, get_client, stream_chat
from app.prompts import IDENTITY, TASKS, load
from app.tools.registry import _SCHEMAS

#: Two non-numeric facts and one number. The number is the control: the
#: existing rule already covers it, so it should be found regardless.
QUESTIONS = [
    "What is the difference between a B-1 and a B-2 visa?",
    "How does the STEM OPT extension work?",
    "What is the H-1B filing fee?",
]

#: A candidate replacement for the lookup rule, to test without editing files.
CANDIDATE = """
LOOK THINGS UP BEFORE YOU ANSWER

Before you answer any substantive question about US immigration, consult a
tool. This applies to every fact, not only to numbers: eligibility rules, form
requirements, definitions, conditions, and procedures all change, and your
memory of them is not a source you may rely on.

If you answer a factual question without having looked anything up, you are
guessing. Do not guess. Look it up first, then answer from what you found.
"""


async def probe(prompt: str, *, model: str) -> str:
    tools = build_tools(_SCHEMAS)
    messages = ([{"role": "system", "content": prompt}] if prompt else [])
    marks = []
    for question in QUESTIONS:
        calls = []
        try:
            async for delta in stream_chat(
                get_client(), model=model, messages=messages + [{"role": "user", "content": question}], tools=tools
            ):
                if delta.tool_calls:
                    calls = delta.tool_calls
            marks.append("T" if calls else ".")
        except Exception:  # noqa: BLE001 - a probe, not a pipeline
            marks.append("E")
    return "".join(marks)


def variants() -> list[tuple[str, str]]:
    full = IDENTITY + "\n\n" + "\n\n".join(load(t) for t in TASKS)
    candidates = [("(none)", ""), ("identity", IDENTITY)]
    candidates += [(task, IDENTITY + "\n\n" + load(task)) for task in TASKS]
    candidates.append(("FULL", full))
    extra = os.environ.get("PROBE_CANDIDATE", CANDIDATE)
    candidates.append(("FULL + candidate", full + "\n\n" + extra))
    return candidates


async def main() -> None:
    model = os.environ.get("PROBE_MODEL", "deepseek/deepseek-chat")
    print(f"model={model}   questions: B-1/B-2, STEM OPT, H-1B fee   (T = used a tool)")
    for name, prompt in variants():
        print(f"  {name:22} {len(prompt):>6} chars  ->  {await probe(prompt, model=model)}")


if __name__ == "__main__":
    asyncio.run(main())
