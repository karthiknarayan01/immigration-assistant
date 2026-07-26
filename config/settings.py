from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Local model server (Ollama)
    ollama_api_base: str = "http://localhost:11434"

    # Model assignments
    orchestrator_model: str = "qwen2.5:14b"
    specialist_model: str = "qwen2.5:14b"

    # Context window passed to Ollama — keeps KV cache small so the model
    # fully offloads to GPU instead of spilling into slow CPU inference.
    ollama_num_ctx: int = 4096

    # Web search (optional)
    serper_api_key: Optional[str] = None

    # RAG
    chroma_persist_dir: str = "./chroma_db"
    embedding_model: str = "all-MiniLM-L6-v2"
    rag_top_k: int = 8
    community_top_k: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
