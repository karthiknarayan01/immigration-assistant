"""
Builds ADK-compatible model handles pointed at the local Ollama server.

Uses google.adk.models.lite_llm.LiteLlm with the "ollama_chat/<model>"
routing prefix — this is the pattern proven to work against a local
`ollama serve` instance, as opposed to faking an OpenAI-compatible base
URL via environment variables.
"""
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from config.settings import settings

# Local models are far more prone to skipping tool calls, hallucinating
# citations, or drifting into another language when sampling with
# temperature > 0. Every agent in this project pins temperature=0.
ZERO_TEMP_CONFIG = types.GenerateContentConfig(temperature=0.0)


def get_model(model_name: str) -> LiteLlm:
    """Returns a LiteLlm model handle routed to the local Ollama server."""
    return LiteLlm(
        model=f"ollama_chat/{model_name}",
        api_base=settings.ollama_api_base,
        num_ctx=settings.ollama_num_ctx,
    )
