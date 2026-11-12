import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.urls import is_valid_path

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


class RequestIdMiddleware:
    """Attach a request id (from a trusted-format header or freshly generated) to every request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get("X-Request-ID", "")
        request.request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class ApiOptionalSlashMiddleware:
    """Serve /v1/foo the same as /v1/foo/ instead of a 301 (which drops POST bodies and isn't JSON).

    Only rewrites when the slashed path is a real route, so unknown paths still 404 as JSON.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path_info
        if path.startswith("/v1/") and not path.endswith("/"):
            urlconf = getattr(request, "urlconf", None)
            if not is_valid_path(path, urlconf) and is_valid_path(f"{path}/", urlconf):
                request.path_info = f"{path}/"
                request.path = f"{request.path}/"
        return self.get_response(request)
