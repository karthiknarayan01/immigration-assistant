"""End-to-end check that a voice session actually becomes ready.

Exists because "the connection opened" is not the same as "the session
started". An earlier WebRTC check verified only ICE connectivity and passed
while the browser hung on "connecting" forever. Run this against localhost
before deploying, and against the deployed URL after.

Usage:
    uv run python scripts/check_handshake.py [base_url]
"""

import asyncio
import sys
import uuid

import websockets

from pipecat.frames.frames import OutputTransportMessageUrgentFrame
from pipecat.serializers.protobuf import ProtobufFrameSerializer

READY_TIMEOUT_SECS = 60


async def main(base_url: str) -> int:
    ws_url = base_url.rstrip("/").replace("http", "ws", 1) + "/ws"
    print(f"connecting to {ws_url}")

    serializer = ProtobufFrameSerializer()
    # The serializer needs a StartFrame's sample rates before use.
    from pipecat.frames.frames import StartFrame

    await serializer.setup(StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=24000))

    async with websockets.connect(ws_url, max_size=None) as ws:
        print("  socket open")

        client_ready = {
            "label": "rtvi-ai",
            "type": "client-ready",
            "id": str(uuid.uuid4()),
            "data": {"version": "1.0.0"},
        }
        payload = await serializer.serialize(
            OutputTransportMessageUrgentFrame(message=client_ready)
        )
        await ws.send(payload)
        print("  -> client-ready")

        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_SECS
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            frame = await serializer.deserialize(raw)
            if frame is None:
                continue
            message = getattr(frame, "message", None)
            if isinstance(message, dict):
                kind = message.get("type")
                print(f"  <- {kind}")
                if kind == "bot-ready":
                    print("\nPASS: session became ready")
                    return 0

    print(f"\nFAIL: no bot-ready within {READY_TIMEOUT_SECS}s — this is what "
          "the browser experiences as a permanent 'connecting'.")
    return 1


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    raise SystemExit(asyncio.run(main(url)))
