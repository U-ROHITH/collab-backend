"""What logged-out visitors see (discovery lists, creator and campaign pages) is the same for everyone, so it's
served from Redis, next to the app, instead of a database round trip to Mumbai. Any write to the data behind those
pages bumps a version, so the next request rebuilds; entries also expire after a minute (bulk .update() calls
don't send signals)."""

from functools import wraps
from typing import Any

from django.core.cache import cache
from rest_framework.response import Response

TTL_SECONDS = 60
_VERSION_KEY = "public:v"
WATCHED_MODELS = (
    "creators.Creator",
    "creators.CreatorPackage",
    "creators.CreatorMedia",
    "campaigns.Campaign",
    "campaigns.Bid",
    "orders.Review",
    "brands.Brand",
)


def cache_for_guests(view: Any) -> Any:
    @wraps(view)
    def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Response:
        if getattr(request.user, "is_authenticated", False):
            return view(self, request, *args, **kwargs)
        key = f"public:{cache.get_or_set(_VERSION_KEY, 1, None)}:{request.get_full_path()}"
        if (data := cache.get(key)) is not None:
            return Response(data)
        response = view(self, request, *args, **kwargs)
        if response.status_code == 200:
            cache.set(key, response.data, TTL_SECONDS)
        return response

    return wrapper


def invalidate(**_: Any) -> None:
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:  # key missing (evicted or first write)
        cache.set(_VERSION_KEY, 2, None)
