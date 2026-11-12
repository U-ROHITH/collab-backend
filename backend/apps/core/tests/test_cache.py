"""The Redis cache must share one pool per process and never turn a Redis outage into a 500."""

from apps.core.cache import ResilientRedisCache, SharedPoolRedisCacheClient

UNREACHABLE = "redis://127.0.0.1:1/0"  # nothing listens on port 1


def _cache() -> ResilientRedisCache:
    return ResilientRedisCache(UNREACHABLE, {})


def test_new_cache_objects_reuse_one_pool() -> None:
    # Under ASGI Django builds a cache object per request; they must not each open a pool.
    a = _cache()._cache._get_connection_pool(write=True)
    b = _cache()._cache._get_connection_pool(write=False)
    assert a is b
    assert isinstance(_cache()._cache, SharedPoolRedisCacheClient)


def test_pool_is_bounded_and_blocking() -> None:
    import redis

    pool = _cache()._cache._get_connection_pool(write=True)
    assert isinstance(pool, redis.BlockingConnectionPool)
    assert pool.max_connections == 10


def test_redis_outage_falls_back_to_memory_and_keeps_counting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from apps.core import cache as cache_module

    monkeypatch.setattr(cache_module.ResilientRedisCache, "_redis_down_until", 0.0)
    cache = _cache()
    cache.delete("k")
    assert cache.get("k") is None and cache.get("k", "fallback") == "fallback"
    assert cache.add("k", 1, 60) is True
    assert cache.add("k", 1, 60) is False  # still counts: lockouts and throttles keep working
    assert cache.incr("k") == 2
    cache.set("other", "v", 60)
    assert cache.get("other") == "v" and cache.get_many(["other"]) == {"other": "v"}
    assert cache.delete("other") is True and cache.has_key("other") is False


def test_breaker_skips_redis_after_an_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from apps.core import cache as cache_module

    monkeypatch.setattr(cache_module.ResilientRedisCache, "_redis_down_until", 0.0)
    cache = _cache()
    cache.get("x")  # fails, opens the breaker
    calls = []
    monkeypatch.setattr(cache_module.RedisCache, "get", lambda *a, **k: calls.append(1))
    cache.get("x")
    assert calls == []  # Redis not tried while the breaker is open
