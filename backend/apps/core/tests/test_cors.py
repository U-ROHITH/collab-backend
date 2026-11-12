"""CORS: the live frontend and its Vercel previews are allowed, other sites aren't."""

import re

import pytest
from django.test import Client

LIVE = "https://linkloom-frontend.vercel.app"
PREVIEW_PATTERN = r"^https://linkloom-frontend-[a-z0-9-]+\.vercel\.app$"


@pytest.fixture
def cors(settings):  # type: ignore[no-untyped-def]
    settings.CORS_ALLOW_ALL_ORIGINS = False
    settings.CORS_ALLOWED_ORIGINS = [LIVE]
    settings.CORS_ALLOWED_ORIGIN_REGEXES = [PREVIEW_PATTERN]


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        (LIVE, True),
        ("https://linkloom-frontend-git-main-team.vercel.app", True),
        ("https://linkloom-frontend-a1b2c3d4-team.vercel.app", True),
        ("https://evil.example.com", False),
        ("https://linkloom-frontend.vercel.app.evil.com", False),
        ("http://linkloom-frontend-x.vercel.app", False),
    ],
)
@pytest.mark.django_db
def test_cors_origins(cors: None, origin: str, allowed: bool) -> None:
    resp = Client().options("/v1/campaigns/", HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET")
    assert (resp.headers.get("Access-Control-Allow-Origin") == origin) is allowed


def test_preview_pattern_is_anchored() -> None:
    assert re.fullmatch(PREVIEW_PATTERN[1:-1], "linkloom-frontend.vercel.app") is None
