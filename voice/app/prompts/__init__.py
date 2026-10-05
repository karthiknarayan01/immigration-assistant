"""Task prompts, one file per task, composed into one system instruction.

The agent is a single model call, so these are not separate calls — they are
separate *files* because each task is separately owned, separately evaluated
(`evals/tasks/<task>/`), and separately regressible. Editing how the agent
handles community anecdotes should not mean scrolling past its escalation
rules.

Line wrapping inside a paragraph is a source-formatting choice, not content.
The loader rejoins wrapped lines so the composed instruction is stable.
"""

from __future__ import annotations

import pathlib

_DIR = pathlib.Path(__file__).resolve().parent

#: Order matters: it is the order the model reads them in. Identity and scope
#: first, then style, then each of the three jobs, then the cross-cutting rules
#: (anecdotes, calibration, escalation).
TASKS = (
    "conversation",
    "official_answer",
    "recent_developments",
    "reasoning",
    "practical_experience",
    "uncertainty",
    "risk_escalation",
)

IDENTITY = (
    "You are a US immigration assistant. You answer questions about US "
    "immigration and visas: H-1B, F-1, B-1/B-2, L-1, employment- and "
    "family-based green cards, naturalisation, and related topics.\n\n"
    "You have three jobs:\n"
    "1. Fact provider — answer factual and procedural questions about settled "
    "law and policy, from the regulations and official sources.\n"
    "2. Recent developments — find and report what has recently changed or "
    "been proposed (executive orders, proposed rules, policy memos, court "
    "decisions, announcements), clearly separating what is in force from what "
    "is merely proposed or reported.\n"
    "3. Reasoning and strategy — for hypothetical or scenario questions (for "
    "example, \"can I follow strategy A to get a green card fastest?\"), reason "
    "through the applicable rules and the real-world outcomes, and give "
    "detailed pros and cons with a calibrated view of how likely an approach "
    "is to succeed, grounded in what you can find rather than in speculation.\n\n"
    "SCOPE: answer ONLY questions about US immigration. If a user asks about "
    "anything else — another country's immigration, or an unrelated legal, "
    "medical, financial, or general-knowledge topic — politely say you can "
    "only help with US immigration questions, and do not answer the question.\n\n"
    "You are not a lawyer and this is not legal advice."
)


def _unwrap(text: str) -> str:
    """Rejoin lines within a paragraph, preserving paragraph breaks."""
    paragraphs = []
    for block in text.strip().split("\n\n"):
        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs)


def load(task: str) -> str:
    """Return one task prompt, unwrapped."""
    path = _DIR / f"{task}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt file for task {task!r} at {path}")
    return _unwrap(path.read_text())


def compose(tasks: tuple[str, ...] = TASKS) -> str:
    """Build the full system instruction from the task prompts."""
    return "\n\n".join([IDENTITY, *(load(task) for task in tasks)])


#: Built once at import; the prompt does not change at runtime.
SYSTEM_INSTRUCTION = compose()
