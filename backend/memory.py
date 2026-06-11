"""
ChromaDB wrapper — document chunk store and per-session chat memory.

Collection naming:
  docs:  p_{patient_id[:8]}_docs
  chat:  p_{patient_id[:8]}_c_{session_id[:8]}

ChromaDB limits collection names to 63 characters, so we use only the
first 8 hex chars of each UUID (sufficient to avoid collisions in practice).
"""

import asyncio
import importlib.util
import logging
import os
import threading
from typing import Any, Optional

# Disable Chroma telemetry explicitly; some builds still emit telemetry events
# even when anonymized_telemetry is false.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# The null telemetry implementation module does not exist in all Chroma versions.
# Only set telemetry implementation overrides when that module is available.
if importlib.util.find_spec("chromadb.telemetry.product.null") is not None:
    os.environ.setdefault("CHROMA_PRODUCT_TELEMETRY_IMPL", "chromadb.telemetry.product.null.NullTelemetry")
    os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "chromadb.telemetry.product.null.NullTelemetry")

import chromadb
from chromadb.config import Settings

from config import DATA_PATH

_client: Optional[chromadb.PersistentClient] = None
_chroma_lock = threading.RLock()
_logger = logging.getLogger("uvicorn.error")


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        db_path = str(DATA_PATH / "memory_db")
        _client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _patient_collection_prefix(patient_id: str) -> str:
    """Common prefix shared by all of a patient's collections (docs + chats)."""
    return f"p_{patient_id[:8]}"


def _docs_collection_name(patient_id: str) -> str:
    return f"{_patient_collection_prefix(patient_id)}_docs"


def _chat_collection_name(patient_id: str, session_id: str) -> str:
    return f"{_patient_collection_prefix(patient_id)}_c_{session_id[:8]}"


# ---------------------------------------------------------------------------
# Document chunk store
# ---------------------------------------------------------------------------

def _upsert_doc_chunks_sync(
    patient_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
    ids: list[str],
) -> None:
    try:
        with _chroma_lock:
            client = _get_client()
            col = client.get_or_create_collection(
                name=_docs_collection_name(patient_id),
                metadata={"hnsw:space": "cosine"},
            )
            col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
    except StopIteration:
        _logger.warning("ChromaDB StopIteration during docs upsert; skipping this upsert call")


async def upsert_doc_chunks(
    patient_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
    ids: list[str],
) -> None:
    await asyncio.to_thread(
        _upsert_doc_chunks_sync, patient_id, chunks, embeddings, metadatas, ids
    )


def _query_docs_sync(
    patient_id: str,
    embedding: list[float],
    n_results: int,
    document_type: Optional[str] = None,
) -> list[dict]:
    with _chroma_lock:
        client = _get_client()
        col_name = _docs_collection_name(patient_id)
        try:
            col = client.get_collection(col_name)
        except Exception:
            return []

    where: Optional[dict] = None
    if document_type and document_type != "unknown":
        where = {"$and": [
            {"patient_id": {"$eq": patient_id}},
            {"document_type": {"$eq": document_type}},
        ]}
    else:
        where = {"patient_id": {"$eq": patient_id}}

    with _chroma_lock:
        try:
            results = col.query(
                query_embeddings=[embedding],
                n_results=min(n_results, col.count() or 1),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except StopIteration:
            _logger.warning("ChromaDB StopIteration during docs query; returning empty results")
            return []
    items = []
    for i, doc in enumerate(results["documents"][0]):
        items.append({
            "text": doc,
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return items


async def query_docs(
    patient_id: str,
    embedding: list[float],
    n_results: int,
    document_type: Optional[str] = None,
) -> list[dict]:
    return await asyncio.to_thread(
        _query_docs_sync, patient_id, embedding, n_results, document_type
    )


def _drop_docs_collection_sync(patient_id: str) -> None:
    with _chroma_lock:
        client = _get_client()
        col_name = _docs_collection_name(patient_id)
        try:
            client.delete_collection(col_name)
        except Exception:
            pass


async def drop_docs_collection(patient_id: str) -> None:
    await asyncio.to_thread(_drop_docs_collection_sync, patient_id)


def _delete_doc_chunks_sync(patient_id: str, document_id: str) -> None:
    with _chroma_lock:
        client = _get_client()
        col_name = _docs_collection_name(patient_id)
        try:
            col = client.get_collection(col_name)
        except Exception:
            return
        try:
            col.delete(where={"document_id": {"$eq": document_id}})
        except StopIteration:
            _logger.warning("ChromaDB StopIteration during doc-chunk delete; skipping")
        except Exception:
            _logger.warning(
                "ChromaDB error during doc-chunk delete patient_id=%s document_id=%s",
                patient_id,
                document_id,
                exc_info=True,
            )


async def delete_doc_chunks(patient_id: str, document_id: str) -> None:
    """Remove all chunks belonging to a single document from the docs collection."""
    await asyncio.to_thread(_delete_doc_chunks_sync, patient_id, document_id)


# ---------------------------------------------------------------------------
# Chat semantic memory
# ---------------------------------------------------------------------------

def _upsert_chat_exchange_sync(
    patient_id: str,
    session_id: str,
    exchange_id: str,
    text: str,
    embedding: list[float],
    metadata: dict,
) -> None:
    try:
        with _chroma_lock:
            client = _get_client()
            col = client.get_or_create_collection(
                name=_chat_collection_name(patient_id, session_id),
                metadata={"hnsw:space": "cosine"},
            )
            col.upsert(
                ids=[exchange_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[metadata],
            )
    except StopIteration:
        _logger.warning("ChromaDB StopIteration during chat upsert; skipping this upsert call")


async def upsert_chat_exchange(
    patient_id: str,
    session_id: str,
    exchange_id: str,
    text: str,
    embedding: list[float],
    metadata: dict,
) -> None:
    await asyncio.to_thread(
        _upsert_chat_exchange_sync,
        patient_id,
        session_id,
        exchange_id,
        text,
        embedding,
        metadata,
    )


def _query_chat_sync(
    patient_id: str,
    session_id: str,
    embedding: list[float],
    n_results: int,
) -> list[dict]:
    with _chroma_lock:
        client = _get_client()
        col_name = _chat_collection_name(patient_id, session_id)
        try:
            col = client.get_collection(col_name)
        except Exception:
            return []

    with _chroma_lock:
        count = col.count()
    if count == 0:
        return []

    with _chroma_lock:
        try:
            results = col.query(
                query_embeddings=[embedding],
                n_results=min(n_results, count),
                where={
                    "$and": [
                        {"patient_id": {"$eq": patient_id}},
                        {"chat_session_id": {"$eq": session_id}},
                    ]
                },
                include=["documents", "metadatas", "distances"],
            )
        except StopIteration:
            _logger.warning("ChromaDB StopIteration during chat query; returning empty results")
            return []
    items = []
    for i, doc in enumerate(results["documents"][0]):
        items.append({
            "text": doc,
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return items


async def query_chat(
    patient_id: str,
    session_id: str,
    embedding: list[float],
    n_results: int,
) -> list[dict]:
    return await asyncio.to_thread(
        _query_chat_sync, patient_id, session_id, embedding, n_results
    )


def _delete_chat_collection_sync(patient_id: str, session_id: str) -> None:
    with _chroma_lock:
        client = _get_client()
        col_name = _chat_collection_name(patient_id, session_id)
        try:
            client.delete_collection(col_name)
        except Exception:
            pass


async def delete_chat_collection(patient_id: str, session_id: str) -> None:
    await asyncio.to_thread(_delete_chat_collection_sync, patient_id, session_id)


def _delete_all_patient_collections_sync(patient_id: str) -> None:
    client = _get_client()
    prefix = _patient_collection_prefix(patient_id)
    try:
        collections = client.list_collections()
    except StopIteration:
        _logger.warning("ChromaDB StopIteration while listing collections; skipping collection cleanup")
        return

    for col in collections:
        # Chroma list_collections has changed across versions:
        # sometimes it returns collection objects, sometimes names.
        col_name: Optional[str] = None
        if isinstance(col, str):
            col_name = col
        else:
            try:
                maybe_name = col.name
                if isinstance(maybe_name, str):
                    col_name = maybe_name
            except Exception:
                col_name = None

        if col_name and col_name.startswith(prefix):
            try:
                client.delete_collection(col_name)
            except Exception:
                pass


async def delete_all_patient_collections(patient_id: str) -> None:
    await asyncio.to_thread(_delete_all_patient_collections_sync, patient_id)
