import sys

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.bot import run_bot
from app.config import settings

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
