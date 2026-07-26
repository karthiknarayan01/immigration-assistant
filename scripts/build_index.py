"""Run this once before starting the agent to build the RAG index."""
import sys

sys.path.insert(0, ".")
from rag.indexer import build_index

if __name__ == "__main__":
    print("Building immigration knowledge index (~5 minutes)...")
    build_index()
    print("Done. Start Ollama (ollama serve) then run: adk run immigration_agent")
