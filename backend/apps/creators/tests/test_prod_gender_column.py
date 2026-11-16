"""Production's creators_creator has the dev branch's gender column (nullable varchar(20) + an enum check).
This code writes '' / free text there, so creator sign-up 500'd. Migration 0005 must reconcile the shape."""

import importlib

import pytest
from django.db import IntegrityError, connection, transaction

from apps.creators.models import Creator
from apps.identity.models import User

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.creators.migrations.0005_creator_dob_gender_social")


def _recreate_production_shape() -> None:
    with connection.cursor() as cur:
        cur.execute('ALTER TABLE "creators_creator" DROP COLUMN "gender"')
        cur.execute('ALTER TABLE "creators_creator" ADD COLUMN "gender" varchar(20) NULL')
        cur.execute(
            'ALTER TABLE "creators_creator" ADD CONSTRAINT "creator_gender_valid" CHECK ("gender" IS NULL OR '
            "\"gender\" IN ('male', 'female', 'non_binary', 'other', 'prefer_not_to_say'))"
        )


def _new_creator(email: str, gender: str = "") -> Creator:
    user = User.objects.create_user(email=email)
    return Creator.objects.create(user=user, display_name="User Name", slug=email.split("@")[0], gender=gender)


def test_creator_insert_fails_on_production_shape_then_works_after_migration() -> None:
    _recreate_production_shape()
    with pytest.raises(IntegrityError), transaction.atomic():  # what production is doing today
        _new_creator("before@example.com")

    migration.forwards(None, connection.schema_editor())

    assert _new_creator("after@example.com").pk
    assert _new_creator("free.text@example.com", gender="Prefers to self-describe").pk


def test_existing_null_genders_become_blank() -> None:
    _recreate_production_shape()
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE creators_creator ALTER COLUMN gender DROP NOT NULL")
    creator = _new_creator("legacy@example.com", gender="female")
    with connection.cursor() as cur:
        cur.execute("UPDATE creators_creator SET gender = NULL WHERE id = %s", [creator.pk])
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # flush deferred FK checks so ALTER TABLE can run

    migration.forwards(None, connection.schema_editor())

    creator.refresh_from_db()
    assert creator.gender == ""


def test_forwards_is_idempotent_on_a_fresh_database() -> None:
    migration.forwards(None, connection.schema_editor())
    migration.forwards(None, connection.schema_editor())
    assert _new_creator("fresh@example.com").pk
