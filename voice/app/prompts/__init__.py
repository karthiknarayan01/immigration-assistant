"""Task prompts, one file per task, composed into one system instruction.

The agent is a single model call, so these are not separate calls — they are
separate *files* because each task is separately owned, separately evaluated
(`evals/tasks/<task>/`), and separately regressible. Editing how the agent
handles community anecdotes should not mean scrolling past its escalation
rules.

Line wrapping inside a paragraph is a source-formatting choice, not content.
The loader rejoins wrapped lines so the composed instruction is identical to
the single hand-maintained string this replaced — the eval benchmark depends
on that equivalence, so `tests/test_prompts.py` asserts it.
"""

from __future__ import annotations

import pathlib

_DIR = pathlib.Path(__file__).resolve().parent

#: Order matters: it is the order the model reads them in, and it matches the
#: prompt this replaced. Identity first, then how to speak, then what to say.
TASKS = (
    "conversation",
    "official_answer",
    "practical_experience",
    "uncertainty",
    "risk_escalation",
)

IDENTITY = (
    "You are a voice assistant that helps people understand US immigration "
    "questions: H-1B, F-1, B-1/B-2, L-1, and employment-based green cards."
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

# Not composed into the system instruction: these drive separate calls.
TURN_COMPLETION_INSTRUCTIONS = _unwrap((_DIR / "_turn_completion.md").read_text())
GREETING_INSTRUCTION = _unwrap((_DIR / "_greeting.md").read_text())
