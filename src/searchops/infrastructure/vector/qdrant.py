"""
Qdrant Vector Store client and repository wrappers with IVectorStore implementation & deterministic UUIDs.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from searchops.config.settings import Settings, get_settings
from searchops.core.exceptions.infrastructure import VectorStoreError
from searchops.core.interfaces.storage import IVectorStore
from searchops.llm.tokenizer import truncate_by_tokens

log = structlog.get_logger(__name__)

_qdrant_client: AsyncQdrantClient | None = None


def get_qdrant_client(settings: Settings | None = None) -> AsyncQdrantClient:
    """Return or initialize the global AsyncQdrantClient singleton."""
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    cfg = settings or get_settings()
    mem_cfg = cfg.memory

    if mem_cfg.qdrant_host == ":memory:":
        log.info("Initializing in-memory local Qdrant client")
        _qdrant_client = AsyncQdrantClient(location=":memory:")
    else:
        api_key = mem_cfg.qdrant_api_key.get_secret_value() if mem_cfg.qdrant_api_key else None
        _qdrant_client = AsyncQdrantClient(
            host=mem_cfg.qdrant_host,
            port=mem_cfg.qdrant_port,
            grpc_port=mem_cfg.qdrant_grpc_port,
            prefer_grpc=mem_cfg.qdrant_prefer_grpc,
            api_key=api_key,
            timeout=mem_cfg.qdrant_timeout,
        )
        log.info("Qdrant client initialized", host=mem_cfg.qdrant_host, port=mem_cfg.qdrant_port)
    return _qdrant_client


import hashlib


class QdrantVectorRepository(IVectorStore):
    """Repository wrapper for Qdrant Vector Store implementing IVectorStore port."""

    def __init__(self, client: AsyncQdrantClient | None = None) -> None:
        self.client = client or get_qdrant_client()

    async def init_collection(self, collection_name: str = "research_chunks", vector_size: int = 1536) -> None:
        """Initialize Qdrant collection with Cosine similarity if it does not exist."""
        try:
            collections = await self.client.get_collections()
            existing = {c.name for c in collections.collections}
            if collection_name not in existing:
                await self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
                log.info("Qdrant collection created", collection=collection_name, size=vector_size)
        except Exception as exc:
            log.warning("Qdrant collection initialization warning", error=str(exc))

    @staticmethod
    def chunk_document(
        url: str,
        text: str,
        chunk_tokens: int = 250,
        overlap_tokens: int = 25,
    ) -> list[dict[str, Any]]:
        """Split text into semantic chunks with overlap and deterministic content-hash UUIDs."""
        if not text.strip():
            return []

        chunks: list[dict[str, Any]] = []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        current_paras: list[str] = []
        current_len = 0
        chunk_index = 0

        for para in paragraphs:
            para_len = len(para)
            if current_len + para_len < chunk_tokens * 4:
                current_paras.append(para)
                current_len += para_len
            else:
                chunk_text = "\n\n".join(current_paras)
                if chunk_text.strip():
                    content = truncate_by_tokens(chunk_text, chunk_tokens)
                    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}:{content_hash}"))
                    chunks.append({
                        "chunk_id": chunk_id,
                        "url": url,
                        "chunk_index": chunk_index,
                        "content": content,
                    })
                    chunk_index += 1

                # Carry over overlap paragraphs if requested
                overlap_chars = overlap_tokens * 4
                overlap_paras: list[str] = []
                acc = 0
                for p in reversed(current_paras):
                    if acc + len(p) <= overlap_chars:
                        overlap_paras.insert(0, p)
                        acc += len(p)
                    else:
                        break

                current_paras = overlap_paras + [para]
                current_len = sum(len(p) for p in current_paras)

        if current_paras:
            chunk_text = "\n\n".join(current_paras)
            if chunk_text.strip():
                content = truncate_by_tokens(chunk_text, chunk_tokens)
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}:{content_hash}"))
                chunks.append({
                    "chunk_id": chunk_id,
                    "url": url,
                    "chunk_index": chunk_index,
                    "content": content,
                })

        return chunks

    async def upsert_chunks(
        self,
        collection_name: str,
        points: list[models.PointStruct],
    ) -> None:
        """Upsert points into Qdrant collection."""
        if not points:
            return
        try:
            await self.client.upsert(collection_name=collection_name, points=points)
            log.info("Upserted points into Qdrant", collection=collection_name, count=len(points))
        except Exception as exc:
            log.error("Failed to upsert points into Qdrant", collection=collection_name, error=str(exc))

    async def search_similar(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Perform dense vector search to retrieve top-K relevant chunks."""
        try:
            results = await self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                {
                    "score": hit.score,
                    "payload": hit.payload or {},
                }
                for hit in results
            ]
        except Exception as exc:
            log.error("Qdrant similarity search failed", collection=collection_name, error=str(exc))
            return []


async def close_qdrant() -> None:
    """Close the Qdrant client connection."""
    global _qdrant_client
    if _qdrant_client is not None:
        await _qdrant_client.close()
        log.info("Qdrant client closed")
        _qdrant_client = None
