"""The text path is the only path now.

These cover transcript handling and model routing — the parts specific to the
text transport. Answer quality is the eval suite's job, not these tests'.
"""

from app.config import settings
from app.llm import build_tools
from app.prompts import SYSTEM_INSTRUCTION
from app.text_agent import build_messages, models_to_try


def test_history_becomes_alternating_messages():
    messages = build_messages(
        [
            {"role": "user", "content": "I am on an H-1B."},
            {"role": "assistant", "content": "Understood."},
        ],
        "What happens if I am laid off?",
    )
    # system, then user/assistant/user
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "What happens if I am laid off?"
    assert messages[0]["content"] == SYSTEM_INSTRUCTION


def test_empty_history_entries_are_dropped():
    """A placeholder assistant message exists in the UI before text arrives."""
    messages = build_messages(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}],
        "next question",
    )
    assert [m["role"] for m in messages] == ["system", "user", "user"]


def test_roles_pass_through():
    messages = build_messages([{"role": "assistant", "content": "prior answer"}], "q")
    assert messages[1]["role"] == "assistant"


def test_models_to_try_prefers_primary_then_fallback():
    original = (settings.llm_model, settings.llm_fallback_model)
    try:
        settings.llm_model = "primary/model"
        settings.llm_fallback_model = "fallback/model"
        assert models_to_try() == ["primary/model", "fallback/model"]

        settings.llm_fallback_model = ""
        assert models_to_try() == ["primary/model"]

        settings.llm_fallback_model = "primary/model"  # duplicate
        assert models_to_try() == ["primary/model"]
    finally:
        settings.llm_model, settings.llm_fallback_model = original


def test_tool_declarations_mirror_the_production_schemas():
    from app.tools.registry import _SCHEMAS

    declared = {t["function"]["name"] for t in build_tools(_SCHEMAS)}
    assert declared == {schema.name for schema in _SCHEMAS}
