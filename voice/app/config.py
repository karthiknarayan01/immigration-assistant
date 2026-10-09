"""Runtime configuration.

The agent is now model-agnostic: it talks to any OpenAI-compatible endpoint
(OpenRouter by default), so the model is a configuration choice rather than a
code change. A single key and a base URL let you run DeepSeek, Llama, Qwen,
Gemini, or a mix, with an automatic fallback model for when the primary is
down or out of credit.

Search providers are unchanged: each activates only when its key is present,
so the agent degrades to saying "I could not check a live source" rather than
guessing at current policy.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Model routing ──────────────────────────────────────────────────────
    # Any OpenAI-compatible endpoint. OpenRouter is the default because one key
    # reaches hundreds of models with automatic fallback; set base_url to a
    # provider's own endpoint (Groq, Together, DeepSeek) to skip the router fee.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""  # e.g. OPENROUTER_API_KEY
    llm_model: str = "deepseek/deepseek-chat"

    #: Used when the primary model fails with a transient error. Leave blank to
    #: disable fallback.
    llm_fallback_model: str = ""

    #: Optional reasoning-capable model for the strategy/reasoning function.
    #: When blank, the primary model answers those questions too.
    reasoner_model: str = ""

    #: Judge model for evals — stronger than the agent under test.
    judge_model: str = "anthropic/claude-3.5-sonnet"

    #: Optional price per million tokens, used only to put a cost column in the
    #: eval report. Left at 0 deliberately: prices change, and a stale
    #: hardcoded price is worse than no number. Set these to your provider's
    #: current rates to get score-per-dollar in the report.
    llm_price_in_per_mtok: float = 0.0
    llm_price_out_per_mtok: float = 0.0

    #: Bounds the whole turn. A deadline, not a count, so a retry never starts
    #: with too little time left to help.
    model_timeout_secs: float = 60.0

    # ── Search providers ────────────────────────────────────────────────────
    # Each tool activates only if its key is present. uscis.gov returns 403 to
    # datacenter traffic, so official sources are reached through these
    # providers' crawlers, never fetched directly.
    tavily_api_key: str = ""
    exa_api_key: str = ""
    parallel_api_key: str = ""
    xai_api_key: str = ""

    # ── Tool budget ─────────────────────────────────────────────────────────
    tool_timeout_secs: float = 12.0

    # ── Server ──────────────────────────────────────────────────────────────
    # Comma-separated. Lock this to your Vercel domain before going public.
    allowed_origins: str = "*"
    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()
