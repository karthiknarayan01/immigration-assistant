"""Pre-render the phrases spoken while a tool call runs.

Gemini Live emits tool calls in complete silence — verified across three
runs, including with a blunt top-of-prompt instruction and with
Behavior.NON_BLOCKING. Prompting cannot fix it, so the audio has to come
from the pipeline instead.

The clips are generated with the same model and voice the live agent uses,
so they are indistinguishable from its own speech.

Usage:
    uv run python scripts/generate_fillers.py
"""

import array
import asyncio
import pathlib
import re

from google import genai
from google.genai import types

from app.config import settings

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "audio" / "fillers"

# Varied on purpose: one fixed phrase becomes grating within a single
# conversation. Split by tool so the wording matches what is happening.
PHRASES = {
    # Played ~250ms after the user stops talking, before the model has decided
    # anything. Deliberately very short: on a fast turn the real answer starts
    # at ~1.2s, so anything longer than about a second talks over it. These
    # commit to nothing — the model may still be about to ask a clarifying
    # question, so "let me look that up" would be wrong here.
    "ack": [
        "Okay.",
        "Right.",
        "Got it.",
        "Sure.",
    ],
    "official": [
        "Let me check the current guidance on that.",
        "One second, let me look that up.",
        "Let me pull up what USCIS says about this.",
        "Give me a moment to check the latest on that.",
        "Let me make sure I have the current rules here.",
        "Hang on, I'll check that now.",
    ],
    "community": [
        "Let me see what people have actually run into with this.",
        "Give me a second to look for some real experiences.",
        "Let me check what others have reported here.",
    ],
}

INSTRUCTION = (
    "You are a voice. Say exactly the words given, once, in a warm, natural, "
    "unhurried tone, as if reassuring someone mid-conversation. Add nothing."
)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def trim_silence(pcm: bytes, *, threshold: int = 400, keep_ms: int = 40) -> bytes:
    """Strip leading and trailing near-silence from 24 kHz mono s16 audio.

    The model pads some renders with up to two seconds of silence. Played as
    a filler that is indistinguishable from the dead air it exists to cover.
    """
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])

    loud = [i for i, value in enumerate(samples) if abs(value) > threshold]
    if not loud:
        return pcm

    keep = int(24_000 * keep_ms / 1000)
    start = max(0, loud[0] - keep)
    end = min(len(samples), loud[-1] + keep)
    return samples[start:end].tobytes()


async def render(client: genai.Client, text: str) -> bytes:
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=INSTRUCTION,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.gemini_voice
                )
            )
        ),
    )
    model = settings.gemini_model.removeprefix("google/")

    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)])
        )
        chunks: list[bytes] = []
        async for response in session.receive():
            if response.data:
                chunks.append(response.data)
            server_content = response.server_content
            if server_content and server_content.turn_complete:
                break
    return b"".join(chunks)


async def main() -> None:
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project_id,
        location=settings.google_cloud_location,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for group, phrases in PHRASES.items():
        target = OUT_DIR / group
        target.mkdir(exist_ok=True)
        for phrase in phrases:
            path = target / f"{slug(phrase)}.pcm"
            if path.exists():
                # Each render is a live session against Vertex. Adding one
                # phrase should not re-bill the nine already on disk.
                print(f"  have    {path.relative_to(OUT_DIR.parent.parent)}")
                continue
            audio = await render(client, phrase)
            if not audio:
                print(f"  SKIP (no audio): {phrase!r}")
                continue
            audio = trim_silence(audio)
            # Raw 24 kHz mono s16le — the sample rate Gemini Live emits, so
            # the clip needs no conversion before going out the transport.
            path.write_bytes(audio)
            print(f"  {len(audio):>7,} bytes  {path.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
