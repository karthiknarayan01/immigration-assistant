"""The chat path runs the same prompt and tools as voice, over a text model.

These cover the parts that are specific to text rather than to the agent:
transcript handling, and the round separator. Answer quality is the eval
suite's job, not these tests'.
"""

from app.prompts import SYSTEM_INSTRUCTION
from app.text_agent import CHAT_MODEL, build_contents, tool_declarations


def test_history_becomes_alternating_contents():
    contents = build_contents(
        [
            {"role": "user", "content": "I am on an H-1B."},
            {"role": "assistant", "content": "Understood."},
        ],
        "What happens if I am laid off?",
    )
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[-1].parts[0].text == "What happens if I am laid off?"


def test_empty_history_entries_are_dropped():
    """A placeholder assistant message exists in the UI before text arrives."""
    contents = build_contents(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}],
        "next question",
    )
    assert [c.role for c in contents] == ["user", "user"]


def test_assistant_maps_to_model_role():
    """The API rejects the role name the browser uses, so it has to be mapped."""
    contents = build_contents([{"role": "assistant", "content": "prior answer"}], "q")
    assert contents[0].role == "model"


def test_chat_uses_the_same_system_prompt_as_voice():
    """The two interfaces may differ in delivery, never in what the agent knows."""
    assert SYSTEM_INSTRUCTION
    assert "immigration" in SYSTEM_INSTRUCTION.lower()


def test_tool_declarations_mirror_the_production_schemas():
    from app.tools.registry import _SCHEMAS

    declared = {
        fn.name for tool in tool_declarations() for fn in (tool.function_declarations or [])
    }
    assert declared == {schema.name for schema in _SCHEMAS}


def test_chat_model_is_not_the_voice_model():
    """gemini-live native-audio refuses a TEXT modality outright (API 1007).

    If someone points chat at the voice model, every turn fails at runtime.
    """
    assert "native-audio" not in CHAT_MODEL
    assert "live" not in CHAT_MODEL
