import re

from app.prompts import (
    GREETING_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    TASKS,
    TURN_COMPLETION_INSTRUCTIONS,
    compose,
    load,
)


def test_every_task_has_a_prompt_file():
    for task in TASKS:
        assert load(task).strip(), f"{task} prompt is empty"


def test_composition_includes_every_task():
    composed = compose()
    for task in TASKS:
        body = load(task)
        # First real sentence of each file must survive composition.
        first = body.split("\n\n")[0][:60]
        assert first in composed, f"{task} missing from composed prompt"


def test_identity_comes_first():
    assert SYSTEM_INSTRUCTION.startswith("You are a voice assistant")


def test_wrapped_lines_are_rejoined():
    # Source files are wrapped for readability; the model should receive
    # flowing paragraphs, not a ragged column.
    body = load("conversation")
    for paragraph in body.split("\n\n"):
        assert "\n" not in paragraph


def test_safety_triggers_survive_refactor():
    # These are the escalation triggers the evals measure. If a refactor drops
    # one, the unsafe rate rises and this catches it before the eval run does.
    text = SYSTEM_INSTRUCTION.lower()
    for trigger in (
        "removal",
        "unlawful presence",
        "criminal history",
        "misrepresentation",
        "work authorisation that has lapsed",
        "immigration attorney",
    ):
        assert trigger in text, f"escalation trigger missing: {trigger}"


def test_key_behaviours_survive_refactor():
    text = SYSTEM_INSTRUCTION.lower()
    for behaviour in (
        "never use markdown",          # spoken output
        "four to eight sentences",     # answer length
        "never state one from memory", # volatile figures
        "corroborated",                # anecdote handling
        "only an intention",           # no bridge-only turns
    ):
        assert behaviour in text, f"behaviour missing: {behaviour}"


def test_turn_completion_is_not_in_the_system_instruction():
    # It drives a separate classifier call; composing it in would confuse the
    # answering model about whose turn it is judging.
    assert TURN_COMPLETION_INSTRUCTIONS not in SYSTEM_INSTRUCTION


def test_greeting_is_short():
    assert len(re.findall(r"[.!?]", GREETING_INSTRUCTION)) <= 4
