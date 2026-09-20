"""Audio played while a tool call runs.

Gemini Live emits tool calls in complete silence. That was verified three
ways — the original prompt instruction, a blunt "calling the tool without
speaking first is a failure" rule at the top of the system prompt, and
Behavior.NON_BLOCKING — and all three produced no speech at all. The model
will not narrate its own tool use, so the pipeline has to.

Clips are pre-rendered by scripts/generate_fillers.py using the same model
and voice as the live agent, so they sound like the agent rather than a
recorded announcement.
"""

from __future__ import annotations

import pathlib
import random

from loguru import logger

#: Raw mono s16le at the rate Gemini Live emits, so clips need no conversion.
SAMPLE_RATE = 24_000
NUM_CHANNELS = 1

_DIR = pathlib.Path(__file__).resolve().parent / "audio" / "fillers"

#: Which clip set matches which tool. A phrase about checking USCIS would be
#: wrong for a search of what people posted on a forum.
TOOL_GROUPS = {
    "search_official_guidance": "official",
    "search_community_experiences": "community",
}


def _load() -> dict[str, list[bytes]]:
    clips: dict[str, list[bytes]] = {}
    if not _DIR.exists():
        logger.warning(f"no filler audio at {_DIR}; tool calls will be silent")
        return clips
    for group_dir in sorted(_DIR.iterdir()):
        if not group_dir.is_dir():
            continue
        loaded = [p.read_bytes() for p in sorted(group_dir.glob("*.pcm"))]
        if loaded:
            clips[group_dir.name] = loaded
    logger.info(
        "loaded filler audio: "
        + ", ".join(f"{name}={len(items)}" for name, items in clips.items())
    )
    return clips


#: Read once at import — the whole set is well under a megabyte.
_CLIPS = _load()


class FillerPicker:
    """Picks a clip per tool call, avoiding an immediate repeat.

    Hearing the same sentence twice in a row is what makes filler audio feel
    canned, so the last clip played is excluded while alternatives exist.
    """

    def __init__(self) -> None:
        self._last: bytes | None = None

    def for_tool(self, function_name: str) -> bytes | None:
        group = TOOL_GROUPS.get(function_name)
        options = _CLIPS.get(group or "", [])
        if not options:
            return None

        choices = [clip for clip in options if clip is not self._last] or options
        chosen = random.choice(choices)
        self._last = chosen
        return chosen
