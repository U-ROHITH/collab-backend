from typing import Any

from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    """Wrap successful responses as {success, data, error, meta}. Errors/paginated are pre-wrapped."""

    def render(self, data: Any, accepted_media_type: str | None = None, renderer_context: Any = None) -> bytes:
        response = (renderer_context or {}).get("response")
        if response is not None and response.status_code == 204:
            return b""
        already_wrapped = isinstance(data, dict) and "success" in data and "error" in data
        if not already_wrapped:
            data = {"success": True, "data": data, "error": None, "meta": None}
        return super().render(data, accepted_media_type, renderer_context)
