"""Redis cache backend that is safe under ASGI and degrades instead of failing requests.

Two problems with Django's stock RedisCache in this deployment (gunicorn + uvicorn workers):

1. Django keeps cache objects in a context-local, and under ASGI every request is a new context, so every
   request built a new RedisCacheClient with a new connection pool. Old pools were never closed, connections
   piled up until Redis answered "max number of clients reached", and every throttled request returned 500.
   Here the pool lives at process level, keyed by server URL, and is bounded.
2. The cache only backs throttles, login lockouts and realtime tickets. A Redis hiccup shouldn't take sign-in
   down with it. On an error the operation is served by a per-process in-memory cache instead, so throttles
   and lockouts keep counting (per worker), and a short circuit breaker skips Redis for a few seconds so
   requests don't each wait out the connect timeout during an outage.
"""

import logging
import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, ClassVar

from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache, RedisCacheClient

logger = logging.getLogger(__name__)

# Per worker process. Render's free Key Value allows 50 clients; 2 workers x 10 leaves room for the channel layer.
DEFAULT_POOL_OPTIONS: dict[str, Any] = {
    "max_connections": 10,
    "timeout": 2,  # BlockingConnectionPool: wait this long for a free connection instead of erroring
    "socket_connect_timeout": 2,
    "socket_timeout": 2,
    "health_check_interval": 30,
    "retry_on_timeout": True,
}


class SharedPoolRedisCacheClient(RedisCacheClient):
    _shared_pools: ClassVar[dict[str, Any]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, servers: list[str], **options: Any) -> None:
        import redis

        options.setdefault("pool_class", redis.BlockingConnectionPool)
        super().__init__(servers, **{**DEFAULT_POOL_OPTIONS, **options})

    def _get_connection_pool(self, write: bool) -> Any:
        url = self._servers[self._get_connection_pool_index(write)]
        pool = self._shared_pools.get(url)
        if pool is None:
            with self._lock:
                pool = self._shared_pools.get(url)
                if pool is None:
                    pool = self._pool_class.from_url(url, **self._pool_options)
                    self._shared_pools[url] = pool
        return pool


BREAKER_SECONDS = 5


def _with_fallback[F: Callable[..., Any]](method: F) -> F:
    name = method.__name__

    @wraps(method)
    def wrapper(self: "ResilientRedisCache", *args: Any, **kwargs: Any) -> Any:
        if time.monotonic() >= ResilientRedisCache._redis_down_until:
            try:
                return method(self, *args, **kwargs)
            except self._client_errors as exc:
                ResilientRedisCache._redis_down_until = time.monotonic() + BREAKER_SECONDS
                logger.warning(
                    "Redis cache %s failed; using in-memory fallback for %ss: %s", name, BREAKER_SECONDS, exc
                )
        return getattr(ResilientRedisCache._fallback, name)(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


class ResilientRedisCache(RedisCache):
    _fallback: ClassVar[LocMemCache] = LocMemCache("redis-fallback", {"OPTIONS": {"MAX_ENTRIES": 10_000}})
    _redis_down_until: ClassVar[float] = 0.0

    def __init__(self, server: str, params: dict[str, Any]) -> None:
        import redis

        super().__init__(server, params)
        self._class = SharedPoolRedisCacheClient
        self._client_errors = (redis.exceptions.RedisError, OSError)

    get = _with_fallback(RedisCache.get)
    add = _with_fallback(RedisCache.add)
    set = _with_fallback(RedisCache.set)
    touch = _with_fallback(RedisCache.touch)
    delete = _with_fallback(RedisCache.delete)
    get_many = _with_fallback(RedisCache.get_many)
    set_many = _with_fallback(RedisCache.set_many)
    delete_many = _with_fallback(RedisCache.delete_many)
    has_key = _with_fallback(RedisCache.has_key)
    incr = _with_fallback(RedisCache.incr)
