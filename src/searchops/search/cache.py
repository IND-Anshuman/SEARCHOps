import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
import structlog
from typing import Any, List, Optional

from searchops.infrastructure.cache.redis import get_redis_client
from searchops.search.domain.models import NormalizedSearchResult
from searchops.search.contracts import SearchResultItem

log = structlog.get_logger(__name__)

def canonicalize_query(query: str) -> str:
    """Normalize query by stripping whitespace, downcasing, and removing punctuation."""
    q = query.strip().lower()
    # Remove common punctuation
    q = re.sub(r'[^\w\s]', '', q)
    # Collapse multiple whitespaces
    q = re.sub(r'\s+', ' ', q)
    return q.strip()


class RedisDistributedLock:
    """Acquires a lock for a query to avoid cache stampede / redundant external searches."""

    def __init__(self, client: Any, query_hash: str, timeout_sec: float = 10.0) -> None:
        self.client = client
        self.name = f"searchops:lock:search:{query_hash}"
        self.timeout_sec = timeout_sec
        self.token = f"tok_{time.time()}_{hash(query_hash)}"

    async def acquire(self) -> bool:
        """Attempt to acquire Redis lock via NX SET."""
        try:
            res = await self.client.set(
                self.name, 
                self.token, 
                px=int(self.timeout_sec * 1000), 
                nx=True
            )
            return bool(res)
        except Exception:
            return False

    async def release(self) -> None:
        """Release lock safely using Lua script to match token identifier."""
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self.client.eval(script, 1, self.name, self.token)
        except Exception:
            pass


class SearchCache:
    """Handles query caching with exact and Jaccard semantic matching options."""

    def __init__(self, client: Any = None) -> None:
        self.client = client or get_redis_client()

    def _get_key(self, query: str, providers_hash: str) -> str:
        canonical = canonicalize_query(query)
        q_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"searchops:cache:search:{q_hash}:{providers_hash}"

    async def get(self, query: str, providers_hash: str) -> Optional[List[SearchResultItem]]:
        """Retrieve cached query results exactly matching canonicalized parameters."""
        key = self._get_key(query, providers_hash)
        try:
            data = await self.client.get(key)
            if data:
                raw_list = json.loads(data)
                return [SearchResultItem(**item) for item in raw_list]
        except Exception as e:
            log.error("Failed to read from search cache", key=key, error=str(e))
        return None

    async def set(self, query: str, providers_hash: str, results: List[SearchResultItem], ttl_sec: int = 900) -> None:
        """Cache query results under exact key."""
        key = self._get_key(query, providers_hash)
        try:
            serialized = json.dumps([item.model_dump() for item in results])
            await self.client.set(key, serialized, ex=ttl_sec)
        except Exception as e:
            log.error("Failed to write to search cache", key=key, error=str(e))

    async def get_semantic(self, query: str, providers_hash: str, threshold: float = 0.85) -> Optional[List[SearchResultItem]]:
        """Lookup cached queries via Jaccard token similarity for close matches (offline fallback)."""
        # Scan recent search cache keys
        try:
            pattern = f"searchops:cache:search:*:{providers_hash}"
            keys = await self.client.keys(pattern)
            if not keys:
                return None

            q_target_set = set(canonicalize_query(query).split())
            if not q_target_set:
                return None

            for key in keys:
                # To perform similarity, we retrieve the cached results and inspect their original mapped query
                # or match query metadata. For performance, we can extract from a hash table of query mappings.
                # If key maps to a match:
                data = await self.client.get(key)
                if not data:
                    continue
                raw_list = json.loads(data)
                if not raw_list:
                    continue
                # Pick original query context from raw_metadata of first result if present
                first_item = raw_list[0]
                orig_query = first_item.get("raw_metadata", {}).get("original_query", "")
                if not orig_query:
                    continue
                
                q_cached_set = set(canonicalize_query(orig_query).split())
                intersection = q_target_set.intersection(q_cached_set)
                union = q_target_set.union(q_cached_set)
                if not union:
                    continue
                jaccard_score = len(intersection) / len(union)
                
                if jaccard_score >= threshold:
                    log.info("Semantic cache hit via Jaccard match", query=query, matched=orig_query, score=round(jaccard_score, 3))
                    return [SearchResultItem(**item) for item in raw_list]
        except Exception as e:
            log.error("Failed semantic cache lookup", error=str(e))
        return None

    @asynccontextmanager
    async def lock_query(self, query: str, timeout_sec: float = 10.0):
        """Context manager to synchronize parallel identical searches."""
        canonical = canonicalize_query(query)
        q_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        lock = RedisDistributedLock(self.client, q_hash, timeout_sec)
        
        acquired = await lock.acquire()
        if not acquired:
            # Wait briefly and retry once
            await asyncio.sleep(0.5)
            acquired = await lock.acquire()

        try:
            yield acquired
        finally:
            if acquired:
                await lock.release()


# Global search cache instance
search_cache = SearchCache()
