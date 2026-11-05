import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

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
