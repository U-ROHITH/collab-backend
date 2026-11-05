from typing import Any

from rest_framework.pagination import CursorPagination
from rest_framework.response import Response


class EnvelopeCursorPagination(CursorPagination):
    """Opaque cursor pagination (stable under inserts). `?limit=` capped at 50."""

    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 50
    ordering = ("-created_at", "-id")

    def get_paginated_response(self, data: Any) -> Response:
        next_link = self.get_next_link()
        next_cursor = None
        if next_link:
            from urllib.parse import parse_qs, urlparse

            next_cursor = parse_qs(urlparse(next_link).query).get(self.cursor_query_param, [None])[0]
        return Response(
            {
                "success": True,
                "data": data,
                "error": None,
                "meta": {"nextCursor": next_cursor, "hasMore": next_cursor is not None},
            }
        )

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "data": schema,
                "error": {"type": "object", "nullable": True},
                "meta": {
                    "type": "object",
                    "properties": {"nextCursor": {"type": "string", "nullable": True}, "hasMore": {"type": "boolean"}},
                },
            },
        }
