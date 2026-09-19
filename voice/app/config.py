from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vertex AI — the credit-eligible path. Google Cloud credits do NOT apply
    # to Gemini Developer API (AI Studio) keys, so we authenticate as a GCP
    # service account instead. Leave both credential fields blank on Cloud Run
    # to use Application Default Credentials from the attached service account.
    google_cloud_project_id: str = ""
    google_cloud_location: str = "us-east4"
    google_vertex_credentials: str = ""
    google_vertex_credentials_path: str = ""

    gemini_model: str = "google/gemini-live-2.5-flash-native-audio"
    gemini_voice: str = "Charon"

    # Turn detection. Defaults are deliberately more patient than Gemini's:
    # users are disproportionately non-native English speakers, who pause
    # mid-sentence more often and for longer. See bot.py for sensitivity.
    vad_silence_duration_ms: int = 1500
    vad_prefix_padding_ms: int = 300

    # Search providers. Each tool activates only if its key is present, so the
    # agent degrades to answering from its own knowledge rather than failing.
    # Note: uscis.gov blocks datacenter traffic (403), so official sources are
    # reached through these providers' crawlers, never fetched directly.
    tavily_api_key: str = ""
    exa_api_key: str = ""
    firecrawl_api_key: str = ""
    parallel_api_key: str = ""
    xai_api_key: str = ""

    # How long a tool may run before the agent gives up and says so. Voice has
    # no tolerance for dead air, even with filler speech covering the gap.
    tool_timeout_secs: float = 8.0

    # Browser clients POST WebRTC offers cross-origin from the Vercel app.
    allowed_origins: str = "*"

    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()
