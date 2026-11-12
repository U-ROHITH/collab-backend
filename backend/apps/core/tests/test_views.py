"""Error pages: JSON envelope for the API, Django's HTML only for the admin."""

import json

from django.conf import settings
from django.http import Http404
from django.test import RequestFactory

from apps.core.views import not_found, server_error

rf = RequestFactory()


def test_server_error_is_json_for_api_paths() -> None:
    resp = server_error(rf.get("/v1/campaigns/"))
    body = json.loads(resp.content)
    assert resp.status_code == 500 and body["error"]["code"] == "INTERNAL_ERROR" and body["success"] is False


def test_admin_keeps_html_error_pages() -> None:
    admin = f"/{settings.ADMIN_URL}missing/"
    assert not_found(rf.get(admin), Http404()).headers["Content-Type"].startswith("text/html")
    assert server_error(rf.get(admin)).headers["Content-Type"].startswith("text/html")


def test_not_found_outside_v1_is_json_too() -> None:
    resp = not_found(rf.get("/wp-login.php"), Http404())
    assert resp.status_code == 404 and json.loads(resp.content)["error"]["code"] == "NOT_FOUND"


def test_health_checks_work_with_any_host_and_plain_http(settings) -> None:  # type: ignore[no-untyped-def]
    from django.test import Client

    settings.ALLOWED_HOSTS = ["collab-api-ewju.onrender.com"]
    settings.SECURE_SSL_REDIRECT = True
    client = Client(HTTP_HOST="10.237.26.192:10000")
    resp = client.get("/healthz")
    assert resp.status_code == 200 and json.loads(resp.content)["status"] == "ok"
    assert client.get("/v1/campaigns/").status_code == 400  # other paths still validate the host
