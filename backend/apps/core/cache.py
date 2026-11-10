from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from django.core.cache import cache

PUBLIC_CACHE_TTL = 600  # 10 minutes


def get_creator_cache_key(creator_id: UUID | str) -> str:
    return f"cache:creator:{creator_id}"


def get_cached_creator_detail(creator_id: UUID | str) -> dict[str, Any] | None:
    return cache.get(get_creator_cache_key(creator_id))


def set_cached_creator_detail(creator_id: UUID | str, data: dict[str, Any], timeout: int = PUBLIC_CACHE_TTL) -> None:
    cache.set(get_creator_cache_key(creator_id), data, timeout=timeout)


def invalidate_creator_cache(creator_id: UUID | str) -> None:
    cache.delete(get_creator_cache_key(creator_id))


# --- Campaigns Caching with Cache Versioning ---


def get_campaigns_cache_version() -> int:
    return cache.get("cache:campaigns:version") or 1


def bump_campaigns_cache_version() -> None:
    try:
        if cache.get("cache:campaigns:version") is None:
            cache.set("cache:campaigns:version", 2, timeout=None)
        else:
            cache.incr("cache:campaigns:version")
    except Exception:
        current = cache.get("cache:campaigns:version") or 1
        cache.set("cache:campaigns:version", current + 1, timeout=None)


def get_campaigns_cache_key(query_params: dict[str, Any] | None = None) -> str:
    version = get_campaigns_cache_version()
    if not query_params:
        return f"cache:campaigns:open:v{version}:default"
    # Sort keys for deterministic hash
    sorted_str = json.dumps(query_params, sort_keys=True, default=str)
    params_hash = hashlib.sha256(sorted_str.encode("utf-8")).hexdigest()[:12]
    return f"cache:campaigns:open:v{version}:{params_hash}"


def get_cached_campaigns(query_params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return cache.get(get_campaigns_cache_key(query_params))


def set_cached_campaigns(
    query_params: dict[str, Any] | None,
    data: dict[str, Any],
    timeout: int = PUBLIC_CACHE_TTL,
) -> None:
    cache.set(get_campaigns_cache_key(query_params), data, timeout=timeout)


def invalidate_campaigns_cache() -> None:
    bump_campaigns_cache_version()
