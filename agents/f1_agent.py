from google.adk.agents import LlmAgent

from config.model_client import ZERO_TEMP_CONFIG, get_model
from config.settings import settings
from prompts.visa_specialists import F1_SYSTEM_PROMPT
from tools.processing_times_tool import processing_times_tool
from tools.rag_tool import community_signals_tool, rag_retrieval_tool
from tools.web_search_tool import web_search_tool

f1_agent = LlmAgent(
    name="f1_specialist",
    model=get_model(settings.specialist_model),
    generate_content_config=ZERO_TEMP_CONFIG,
    description="F1 student visa specialist: OPT, CPT, SEVIS, interview reality.",
    instruction=F1_SYSTEM_PROMPT,
    tools=[rag_retrieval_tool, community_signals_tool, web_search_tool, processing_times_tool],
)
