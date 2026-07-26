#!/bin/bash
# Run this once to pull the required Ollama model.
# Requires Ollama installed: https://ollama.com/download
# Default matches .env.example: qwen2.5:14b (~9GB), used for both the
# orchestrator and all visa specialists.
# Override with QWEN_MODEL if you use a different model.

MODEL="${QWEN_MODEL:-qwen2.5:14b}"

echo "Pulling $MODEL (orchestrator + specialists)..."
ollama pull "$MODEL"

echo "Done. Start Ollama with: ollama serve"
echo "Then run the agent with: adk run immigration_agent"
