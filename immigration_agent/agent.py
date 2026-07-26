"""
Root orchestrator. ADK entry point.
Run with: adk run immigration_agent (from the repo root)

All models run locally via Ollama — no paid APIs required.
Make sure Ollama is running (ollama serve) before starting.
"""
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool

from agents.b1b2_agent import b1b2_agent
from agents.eb_agent import eb_agent
from agents.f1_agent import f1_agent
from agents.h1b_agent import h1b_agent
from agents.l1_agent import l1_agent
from config.model_client import ZERO_TEMP_CONFIG, get_model
from config.settings import settings
from prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT

root_agent = LlmAgent(
    name="immigration_intel_orchestrator",
    model=get_model(settings.orchestrator_model),
    generate_content_config=ZERO_TEMP_CONFIG,
    description=(
        "Expert US immigration assistant covering H1B, F1, B1/B2, L1, "
        "EB1, EB2, and EB3. Explains immigration law and real-world practice."
    ),
    instruction=ORCHESTRATOR_SYSTEM_PROMPT,
    tools=[
        # skip_summarization: the specialist's answer goes straight to the
        # user instead of costing an extra LLM call for the orchestrator to
        # relay it. Trade-off: this ends the turn immediately, so only ONE
        # specialist can ever be called per query — see ORCHESTRATOR_SYSTEM_PROMPT.
        AgentTool(agent=h1b_agent, skip_summarization=True),
        AgentTool(agent=f1_agent, skip_summarization=True),
        AgentTool(agent=b1b2_agent, skip_summarization=True),
        AgentTool(agent=l1_agent, skip_summarization=True),
        AgentTool(agent=eb_agent, skip_summarization=True),
    ],
)
