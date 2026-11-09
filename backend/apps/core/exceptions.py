"""Domain errors + the DRF exception handler that renders every error in the standard envelope."""

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exc
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Raised by services. Carries a stable `code` that frontends switch on."""

    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"
    message = "The request could not be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"
    message = "The resource is not in a state that allows this action."


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You are not allowed to perform this action."


class TooManyRequests(DomainError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests. Please try again later."


class ConversationNotAllowed(DomainError):
    status_code = 422
    code = "CONVERSATION_NOT_ALLOWED"
    message = "A brand cannot message a creator without an active order, bid, or proposal."


_DRF_CODES: dict[type[Exception], str] = {
    drf_exc.ValidationError: "VALIDATION_ERROR",
    drf_exc.ParseError: "MALFORMED_REQUEST",
    drf_exc.NotAuthenticated: "AUTH_REQUIRED",
    drf_exc.AuthenticationFailed: "AUTH_INVALID",
    drf_exc.PermissionDenied: "FORBIDDEN",
    drf_exc.NotFound: "NOT_FOUND",
    drf_exc.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    drf_exc.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    drf_exc.Throttled: "RATE_LIMITED",
}


def _envelope(code: str, message: str, request_id: str | None, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "requestId": request_id}
    if details is not None:
        error["details"] = details
    return {"success": False, "data": None, "error": error}


def envelope_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    request = context.get("request")
    request_id = getattr(request, "request_id", None) if request else None

    if isinstance(exc, DomainError):
        return Response(_envelope(exc.code, exc.message, request_id), status=exc.status_code)

    if isinstance(exc, Http404):
        exc = drf_exc.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exc.PermissionDenied()

    response = drf_default_handler(exc, context)
    if response is None:
        # Unhandled -> 500 with a generic message; details only in logs/Sentry.
        logger.exception("Unhandled error", extra={"request_id": request_id})
        return Response(_envelope("INTERNAL_ERROR", "Something went wrong.", request_id), status=500)

    code = next((c for t, c in _DRF_CODES.items() if isinstance(exc, t)), "ERROR")
    if isinstance(exc, (drf_exc.AuthenticationFailed, drf_exc.NotAuthenticated)):
        exc_str = str(getattr(exc, "detail", exc)).lower()
        if "expired" in exc_str or "token_not_valid" in exc_str:
            code = "TOKEN_EXPIRED"

    view = context.get("view")
    if view and getattr(view, "__class__", None).__name__ == "TokenRefreshEnvelopeView" and response.status_code == 401:
        code = "TOKEN_EXPIRED"

    if isinstance(exc, drf_exc.ValidationError):
        response.data = _envelope(code, "Invalid input.", request_id, details=exc.detail)
    else:
        detail = getattr(exc, "detail", "Error.")
        response.data = _envelope(code, str(detail), request_id)
    return response
