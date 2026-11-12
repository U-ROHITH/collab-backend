from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import defaults


def healthz(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def readyz(_request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


def _api_error(request: HttpRequest, status: int, code: str, message: str) -> JsonResponse:
    error = {"code": code, "message": message, "requestId": getattr(request, "request_id", None)}
    return JsonResponse({"success": False, "data": None, "error": error}, status=status)


def not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Unknown routes get the JSON envelope; only the Django admin keeps its HTML page."""
    if request.path.startswith(f"/{settings.ADMIN_URL}"):
        return defaults.page_not_found(request, exception)
    return _api_error(request, 404, "NOT_FOUND", "Not found.")


def server_error(request: HttpRequest) -> HttpResponse:
    """Errors raised outside DRF views (middleware, URL resolving) still answer in the envelope."""
    if request.path.startswith(f"/{settings.ADMIN_URL}"):
        return defaults.server_error(request)
    return _api_error(request, 500, "INTERNAL_ERROR", "Something went wrong.")
