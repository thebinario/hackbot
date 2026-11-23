"""Index and search recon-capability knowledge in the local Chroma store.

This is the RAG half of the hybrid design. The same capability text that the
registry defines is embedded here (with the same local nomic-embed model used
for writeups) so the planner can retrieve "what recon can do" semantically for
the current target, instead of being handed the whole manifest every turn.

It lives in its own Chroma collection (``recon_capabilities``) next to the
``writeups`` collection, so re-indexing one never disturbs the other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ingest.ollama_client import OllamaEmbeddingClient
from app.rag.vector_store import DEFAULT_CHROMA_PERSIST_DIRECTORY
from app.writeups.store import (
    _get_collection,
    embed_document,
    embed_query,
    embedding_format,
)

from .exploit_engines import capability_documents as engine_documents
from .tool_registry import capability_documents

DEFAULT_CAPABILITIES_COLLECTION_NAME = "recon_capabilities"


def index_capabilities(
    client: OllamaEmbeddingClient,
    persist_directory: str | Path = DEFAULT_CHROMA_PERSIST_DIRECTORY,
    collection_name: str = DEFAULT_CAPABILITIES_COLLECTION_NAME,
) -> int:
    """Embed every recon capability and exploitation engine into the collection."""
    docs = capability_documents() + engine_documents()
    collection = _get_collection(persist_directory, collection_name, create=True)
    fmt = embedding_format(client)
    for doc in docs:
        embedding = embed_document(client, doc["text"])
        collection.upsert(
            ids=[doc["id"]],
            embeddings=[embedding],
            documents=[doc["text"]],
            metadatas=[{"embedding_model": client.model, "embedding_format": fmt}],
        )
    return len(docs)


def search_capabilities(
    query: str,
    client: OllamaEmbeddingClient,
    persist_directory: str | Path = DEFAULT_CHROMA_PERSIST_DIRECTORY,
    top_k: int = 4,
    collection_name: str = DEFAULT_CAPABILITIES_COLLECTION_NAME,
) -> list[tuple[str, float]]:
    """Return (capability_text, cosine_distance) pairs, closest first.

    Empty list if the collection is missing or empty, so callers can degrade to
    the full manifest rather than fail.
    """
    if not query.strip() or top_k <= 0:
        return []
    try:
        collection = _get_collection(persist_directory, collection_name, create=False)
    except RuntimeError:
        return []
    if collection.count() == 0:
        return []

    embedding = embed_query(client, query)
    result = collection.query(query_embeddings=[embedding], n_results=top_k)
    documents = (result.get("documents") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    pairs: list[tuple[str, float]] = []
    for document, distance in zip(documents, distances):
        if isinstance(document, str):
            pairs.append((document, float(distance)))
    return pairs
