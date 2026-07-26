from google.adk.agents import LlmAgent

from config.model_client import ZERO_TEMP_CONFIG, get_model
from config.settings import settings
from prompts.visa_specialists import H1B_SYSTEM_PROMPT
from tools.processing_times_tool import processing_times_tool
from tools.rag_tool import community_signals_tool, rag_retrieval_tool
from tools.web_search_tool import web_search_tool

h1b_agent = LlmAgent(
    name="h1b_specialist",
    model=get_model(settings.specialist_model),
    generate_content_config=ZERO_TEMP_CONFIG,
    description="H1B visa specialist: specialty occupation, cap, lottery, RFE patterns.",
    instruction=H1B_SYSTEM_PROMPT,
    tools=[rag_retrieval_tool, community_signals_tool, web_search_tool, processing_times_tool],
)
