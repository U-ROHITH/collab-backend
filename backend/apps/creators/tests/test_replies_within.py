"""`replies_within` is stored as integer hours (production's schema) but the API keeps "1h"/"6h"/"24h"/"3d"."""

import importlib
from typing import Any

import pytest
from django.db import connection

from apps.creators.models import Creator

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.creators.migrations.0004_replies_within_hours")


def test_api_shows_and_accepts_the_short_labels(creator: Any, anon: Any) -> None:
    me = creator.get("/v1/creators/me/").json()["data"]
    assert me["repliesWithin"] == "24h"
    resp = creator.patch("/v1/creators/me/", {"repliesWithin": "3d"}, format="json")
    assert resp.status_code == 200 and resp.json()["data"]["repliesWithin"] == "3d"
    assert Creator.objects.get(pk=me["id"]).replies_within == 72
    public = anon.get(f"/v1/creators/{me['id']}/").json()["data"]
    assert public["repliesWithin"] == "3d"


def test_unknown_label_is_rejected(creator: Any) -> None:
    resp = creator.patch("/v1/creators/me/", {"repliesWithin": "2h"}, format="json")
    assert resp.status_code == 400 and "repliesWithin" in resp.json()["error"]["details"]


def test_text_column_is_converted_and_integer_column_left_alone(creator: Any) -> None:
    pk = creator.get("/v1/creators/me/").json()["data"]["id"]
    with connection.cursor() as cur:  # put the column back into the old text shape
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # flush deferred FK checks so ALTER TABLE is allowed
        cur.execute(
            'ALTER TABLE "creators_creator" ALTER COLUMN "replies_within" TYPE varchar(4) USING '
            "(CASE replies_within WHEN 6 THEN '6h' ELSE '24h' END)"
        )
        cur.execute("UPDATE creators_creator SET replies_within = '6h' WHERE id = %s", [pk])
    migration.to_hours(None, connection.schema_editor())
    migration.to_hours(None, connection.schema_editor())  # idempotent: already integer
    assert Creator.objects.get(pk=pk).replies_within == 6
