"""
Crawls and indexes all sources from rag/sources.py into ChromaDB.
Run once: python scripts/build_index.py
Re-run weekly to refresh content.
"""
from datetime import datetime, timezone

import chromadb
import httpx
from bs4 import BeautifulSoup
from chromadb.utils import embedding_functions
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from rag.chunker import chunk_text
from rag.sources import SOURCES


def get_collection():
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=settings.embedding_model
    )
    return client.get_or_create_collection(
        name="immigration_docs",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def fetch_page(url: str) -> str:
    headers = {"User-Agent": "immigration-assistant/1.0 (educational tool)"}
    resp = httpx.get(url, timeout=20, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["nav", "footer", "script", "style", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def index_source(source: dict, collection):
    logger.info(f"Indexing: {source['name']}")
    try:
        text = fetch_page(source["base_url"])
        chunks = chunk_text(text)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ids, docs, metas = [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(f"{source['id']}_chunk_{i}")
            docs.append(chunk)
            metas.append(
                {
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "url": source["base_url"],
                    "source_type": source["source_type"],
                    "weight": str(source["weight"]),
                    "visa_categories": source["visa_categories"],
                    "date_indexed": date_str,
                }
            )
        collection.upsert(ids=ids, documents=docs, metadatas=metas)
        logger.success(f"Indexed {len(chunks)} chunks from {source['name']}")
    except Exception as e:
        logger.error(f"Failed to index {source['name']}: {e}")


def build_index():
    collection = get_collection()
    for source in SOURCES:
        index_source(source, collection)
    logger.success(f"Index complete. {collection.count()} total chunks.")


if __name__ == "__main__":
    build_index()
