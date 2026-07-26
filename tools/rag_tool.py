"""
RAG retrieval tool. Queries ChromaDB for relevant chunks.
Returns results with source metadata and weight.
"""
from typing import List, Optional

import chromadb
from chromadb.utils import embedding_functions
from google.adk.tools import FunctionTool
from loguru import logger

from config.settings import settings

_collection = None


def get_collection(name="immigration_docs"):
    """Lazily builds the Chroma collection once and caches it.

    Rebuilding the SentenceTransformer embedding function on every call
    reloads the embedding model from disk each time, adding tens of
    seconds to every RAG query — a real cost when running interactively.
    """
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        _collection = client.get_or_create_collection(name=name, embedding_function=ef)
    return _collection


def retrieve_rag_context(
    query: str,
    visa_category: str,
    source_types: Optional[List[str]] = None,
    top_k: Optional[int] = None,
) -> dict:
    """
    Retrieve relevant immigration law context from the vector database.

    Args:
        query: The user's question or topic to search for.
        visa_category: One of H1B, F1, B1B2, L1, EB1, EB2, EB3.
        source_types: Filter by source types. Options: official,
            legal_interpretation, news, community. Defaults to all except community.
        top_k: Number of results to return. Defaults to settings.rag_top_k.

    Returns:
        dict with 'results' list, each containing text, source, url,
        source_type, weight, and relevance_score.
    """
    if top_k is None:
        top_k = settings.rag_top_k
    if source_types is None:
        source_types = ["official", "legal_interpretation", "news"]

    try:
        collection = get_collection()

        where_filter = {
            "$and": [
                {"source_type": {"$in": source_types}},
                {"visa_categories": {"$contains": visa_category}},
            ]
        }

        results = collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            formatted.append(
                {
                    "text": doc,
                    "source_name": meta.get("source_name", "Unknown"),
                    "url": meta.get("url", ""),
                    "source_type": meta.get("source_type", "unknown"),
                    "weight": float(meta.get("weight", 0.5)),
                    "date_indexed": meta.get("date_indexed", "unknown"),
                    "relevance_score": round(1 - dist, 3),
                }
            )

        formatted.sort(key=lambda x: x["weight"] * x["relevance_score"], reverse=True)
        logger.info(f"RAG retrieved {len(formatted)} chunks for '{visa_category}'")
        return {"results": formatted, "total": len(formatted)}

    except Exception as e:
        logger.error(f"RAG retrieval failed: {e}")
        return {"results": [], "total": 0, "error": str(e)}


def retrieve_community_signals(
    query: str,
    visa_category: str,
    top_k: Optional[int] = None,
) -> dict:
    """
    Retrieve community reports (Reddit, Trackitt, VisaJourney) from the
    vector database. Results are flagged as anecdotal — low weight.

    Args:
        query: The user's question.
        visa_category: One of H1B, F1, B1B2, L1, EB1, EB2, EB3.
        top_k: Number of results. Defaults to settings.community_top_k.

    Returns:
        dict with 'results' list, each flagged with is_anecdotal=True.
    """
    if top_k is None:
        top_k = settings.community_top_k

    result = retrieve_rag_context(
        query=query,
        visa_category=visa_category,
        source_types=["community"],
        top_k=top_k,
    )

    for r in result.get("results", []):
        r["is_anecdotal"] = True
        r["disclaimer"] = "Community report — individual experience, not legal advice."

    return result


rag_retrieval_tool = FunctionTool(func=retrieve_rag_context)
community_signals_tool = FunctionTool(func=retrieve_community_signals)
