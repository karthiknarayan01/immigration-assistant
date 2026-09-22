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

#: Short, non-committal clips played before the model has decided anything.
#: Separate from the tool groups because at that point we do not yet know
#: whether a search is coming, so the wording cannot promise one.
ACK_GROUP = "ack"


#: An acknowledgement is queued ahead of the real answer, so it delays that
#: answer by its own length. Past about this, it stops covering the gap and
#: starts being the gap — and on a fast turn it talks over the reply. Renders
#: vary in pace enough that this has to be enforced rather than assumed: the
#: same three words came back at 1.8s while "got it" came back at 0.35s.
MAX_ACK_MS = 800


def _duration_ms(clip: bytes) -> float:
    return 1000 * len(clip) / (SAMPLE_RATE * NUM_CHANNELS * 2)


def _load() -> dict[str, list[bytes]]:
    clips: dict[str, list[bytes]] = {}
    if not _DIR.exists():
        logger.warning(f"no filler audio at {_DIR}; tool calls will be silent")
        return clips
    for group_dir in sorted(_DIR.iterdir()):
        if not group_dir.is_dir():
            continue
        loaded = []
        for path in sorted(group_dir.glob("*.pcm")):
            data = path.read_bytes()
            if group_dir.name == ACK_GROUP and _duration_ms(data) > MAX_ACK_MS:
                logger.warning(
                    f"skipping {path.name}: {_duration_ms(data):.0f}ms is too long "
                    f"for an acknowledgement (max {MAX_ACK_MS}ms)"
                )
                continue
            loaded.append(data)
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
        return self._pick(TOOL_GROUPS.get(function_name))

    def acknowledgement(self) -> bytes | None:
        """A short clip for the gap before the model has decided anything."""
        return self._pick(ACK_GROUP)

    def _pick(self, group: str | None) -> bytes | None:
        options = _CLIPS.get(group or "", [])
        if not options:
            return None

        choices = [clip for clip in options if clip is not self._last] or options
        chosen = random.choice(choices)
        self._last = chosen
        return chosen


def clip_duration_ms(clip: bytes) -> float:
    """How long a clip takes to play, for scheduling what follows it."""
    return 1000 * len(clip) / (SAMPLE_RATE * NUM_CHANNELS * 2)
