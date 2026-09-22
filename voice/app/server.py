import json
import sys

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google import genai
from loguru import logger
from pydantic import BaseModel

from app.bot import run_bot
from app.config import settings
from app.failures import FailureKind, session_failure_message
from app.observability import log_agent_response, log_user_query, new_request
from app.text_agent import StatusEvent, stream_answer

app = FastAPI()

# The browser connects to /ws directly. CORS still matters for any plain HTTP
# the page makes; WebSocket origin checks are handled at the route.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    newMessage: str
    # The browser holds the only copy of the transcript; nothing is stored
    # here, so prior turns have to come back up with each request.
    recentMessages: list[ChatMessage] = []
    summary: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/chat")
async def chat(request: ChatRequest):
    """Stream one answer, with the same status events the voice path emits.

    Server-sent events rather than plain text: the tokens and the "what am I
    doing right now" updates share one ordered stream, so the client cannot
    show a status after the answer it belongs to.
    """
    new_request(session_id="chat")
    log_user_query(request.newMessage)

    history = [m.model_dump() for m in request.recentMessages]
    if request.summary:
        # Older turns arrive summarised rather than in full; the agent should
        # read that as context it already has, not as something the user said.
        history.insert(
            0,
            {
                "role": "user",
                "content": f"Earlier in this conversation: {request.summary}",
            },
        )

    async def events():
        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project_id,
            location=settings.google_cloud_location,
        )
        answer: list[str] = []

        async def on_status(event: StatusEvent | None):
            nonlocal pending_status
            pending_status = (
                _sse("status", {"state": "working", "label": event.label, "round": event.round})
                if event
                else _sse("status", {"state": "idle"})
            )

        pending_status: str | None = None
        try:
            stream = stream_answer(client, history, request.newMessage, on_status=on_status)
            async for chunk in stream:
                if pending_status:
                    yield pending_status
                    pending_status = None
                answer.append(chunk)
                yield _sse("token", {"text": chunk})
            if pending_status:
                yield pending_status
        except Exception as error:  # noqa: BLE001 - the client needs to hear why
            kind = (
                FailureKind.FUNDS
                if "RESOURCE_EXHAUSTED" in str(error) or "429" in str(error)
                else FailureKind.CONNECTIVITY
            )
            logger.exception("chat turn failed")
            yield _sse(
                "error",
                {
                    "kind": kind.value,
                    "message": session_failure_message(kind),
                    "retryable": kind is FailureKind.CONNECTIVITY,
                },
            )
        else:
            log_agent_response("".join(answer))
        finally:
            yield _sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Proxies buffer streamed responses by default, which turns a live
        # answer into one delivered all at once at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("websocket accepted")
    try:
        await run_bot(websocket)
    except WebSocketDisconnect:
        logger.info("websocket disconnected by client")
    except Exception:
        # A crash here would otherwise be silent to the browser, which just
        # sees the socket close and retries forever.
        logger.exception("voice session failed")
    finally:
        logger.info("websocket session ended")


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
