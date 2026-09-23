"""Check every external service the agent depends on, and say what works.

Run this before an eval, before a deploy, or whenever answers look worse
than they should. Quality drops for two very different reasons — a service
is out of credit, or the network is having a bad minute — and they need
opposite responses. Guessing between them wastes a lot of time.

    PYTHONPATH=. uv run python scripts/check_services.py

Exit status is 0 when everything load-bearing works, 1 otherwise, so this
can gate a deploy.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

logger.remove()  # this script does its own reporting

from app.config import settings  # noqa: E402
from app.failures import FailureKind, classify_exception  # noqa: E402
from app.tools import providers  # noqa: E402

OK = "  ok  "
FAIL = " FAIL "
SKIP = " skip "


async def check_vertex() -> tuple[bool, str]:
    """The model. Nothing works without this."""
    if not settings.google_cloud_project_id:
        return False, "GOOGLE_CLOUD_PROJECT_ID is not set"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project_id,
            location=settings.google_cloud_location,
        )
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(temperature=0),
        )
        return True, f"replied {(response.text or '').strip()[:12]!r}"
    except Exception as error:  # noqa: BLE001 - reporting, not handling
        failure = classify_exception(error)
        return False, f"{failure.kind.value} ({failure.detail})"


async def check_provider(name: str) -> tuple[bool | None, str]:
    """Search providers. Each is optional; losing all of them is not."""
    key = {
        "tavily": settings.tavily_api_key,
        "exa": settings.exa_api_key,
        "parallel": settings.parallel_api_key,
    }[name]
    if not key:
        return None, "no key configured"

    client = providers.get_client()
    try:
        if name == "tavily":
            hits = await providers._tavily(client, "H-1B grace period", None, 3)
        elif name == "exa":
            hits = await providers._exa(client, "H-1B grace period", None, 3)
        else:
            hits = await providers._parallel(client, "H-1B grace period", 3)
    except Exception as error:  # noqa: BLE001 - reporting, not handling
        failure = classify_exception(error)
        hint = (
            " — add credit"
            if failure.kind is FailureKind.FUNDS and not failure.retryable
            else " — check the key"
            if failure.kind is FailureKind.AUTH
            else ""
        )
        return False, f"{failure.kind.value} ({failure.detail}){hint}"

    characters = sum(len(hit.text or "") for hit in hits)
    if not hits:
        return False, "reachable but returned nothing"
    # A provider that answers with empty bodies is worse than one that is
    # down: the agent gets citations with no facts in them and fills the
    # gap from memory.
    if characters == 0:
        return False, f"{len(hits)} hits but no text content"
    return True, f"{len(hits)} hits, {characters:,} chars"


async def main() -> int:
    print("Checking services the agent depends on\n")

    ok, detail = await check_vertex()
    print(f"[{OK if ok else FAIL}] vertex ai (model + judge)   {detail}")
    healthy = ok

    provider_states = []
    for name in ("tavily", "exa", "parallel"):
        state, detail = await check_provider(name)
        mark = SKIP if state is None else (OK if state else FAIL)
        print(f"[{mark}] {name:<26} {detail}")
        provider_states.append(state)
    await providers.aclose()

    working = sum(1 for state in provider_states if state)
    print()
    if not ok:
        print("The model is unreachable. Nothing will work until that is fixed.")
    elif working == 0:
        print(
            "No search provider is working. The agent will still answer, but it\n"
            "cannot check anything live — expect it to say so rather than give\n"
            "you current figures."
        )
        healthy = False
    elif working < len([s for s in provider_states if s is not None]):
        print(f"{working} of 3 search providers working. Answers will be thinner than usual.")
    else:
        print("All good.")

    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
