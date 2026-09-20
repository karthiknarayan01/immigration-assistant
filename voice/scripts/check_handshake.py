"""End-to-end check that a voice session actually becomes ready.

Exists because a peer connection completing is not the same thing as a
session starting. An earlier test verified only ICE connectivity and passed
while the real browser client hung on "connecting" forever, because nothing
answered the RTVI handshake. This exercises that handshake.

Usage:
    uv run python scripts/check_handshake.py [base_url]
"""

import asyncio
import json
import sys
import uuid

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription

READY_TIMEOUT_SECS = 45


async def main(base_url: str) -> int:
    offer_url = f"{base_url.rstrip('/')}/api/offer"
    pc = RTCPeerConnection()
    bot_ready = asyncio.get_running_loop().create_future()

    # The browser SDK opens the channel, so the client must create it here too.
    channel = pc.createDataChannel("rtvi-ai")

    @channel.on("message")
    def on_message(raw):
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        print(f"  <- {message.get('type')}")
        if message.get("type") == "bot-ready" and not bot_ready.done():
            bot_ready.set_result(message)

    @channel.on("open")
    def on_open():
        payload = {
            "label": "rtvi-ai",
            "type": "client-ready",
            "id": str(uuid.uuid4()),
            "data": {"version": "1.0.0"},
        }
        print("  -> client-ready")
        channel.send(json.dumps(payload))

    pc.addTransceiver("audio", direction="sendrecv")
    pc.addTransceiver("video", direction="sendrecv")
    await pc.setLocalDescription(await pc.createOffer())

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            offer_url,
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        )
        response.raise_for_status()
        answer = response.json()

    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
    )
    print(f"  signalling ok (pc_id={answer.get('pc_id')})")

    try:
        await asyncio.wait_for(bot_ready, timeout=READY_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        print(f"\nFAIL: no bot-ready within {READY_TIMEOUT_SECS}s — "
              "the session never started, which is what the browser sees as "
              "a permanent 'connecting'.")
        await pc.close()
        return 1

    print("\nPASS: session became ready")
    await pc.close()
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    print(f"checking {url}")
    raise SystemExit(asyncio.run(main(url)))
