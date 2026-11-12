"""Production has extra NOT NULL columns from an unmerged branch's migrations. Inserts from this code must
still work, and unrelated integrity errors must not be disguised as "user does not exist"."""

import importlib

import pytest
from django.db import IntegrityError, connection, transaction

from apps.identity import services
from apps.identity.models import OtpChannel, User

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.core.migrations.0001_defaults_for_unmerged_columns")


def _add_unmerged_columns() -> None:
    with connection.cursor() as cur:
        cur.execute('ALTER TABLE "identity_user" ADD COLUMN "email_verified" boolean NOT NULL')
        cur.execute('ALTER TABLE "creators_creator" ADD COLUMN "social_media_links" jsonb NOT NULL')
        cur.execute('ALTER TABLE "creators_creator" ADD COLUMN "social_accounts" jsonb NOT NULL')


def test_new_users_and_creators_insert_after_defaults_are_set(make_creator) -> None:  # type: ignore[no-untyped-def]
    _add_unmerged_columns()
    with pytest.raises(IntegrityError), transaction.atomic():  # what production was doing
        User.objects.create_user(phone="+919000000001")

    migration.set_defaults(None, connection.schema_editor())

    user = services._get_or_create_user(OtpChannel.SMS, "+919000000002")
    assert user.pk
    assert make_creator("Ananya Rao").user.creator.pk  # creator insert works too
    with connection.cursor() as cur:
        cur.execute("SELECT email_verified FROM identity_user WHERE id = %s", [user.pk])
        assert cur.fetchone()[0] is False


def test_set_defaults_is_a_no_op_when_columns_are_absent() -> None:
    migration.set_defaults(None, connection.schema_editor())  # must not raise
    assert services._get_or_create_user(OtpChannel.SMS, "+919000000003").pk


def test_unrelated_integrity_error_is_not_disguised(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_create(**_kwargs: object) -> User:
        raise IntegrityError('null value in column "something_new"')

    monkeypatch.setattr(User.objects, "create_user", broken_create)
    with pytest.raises(IntegrityError, match="something_new"):
        services._get_or_create_user(OtpChannel.SMS, "+919000000004")


def test_concurrent_first_login_still_returns_the_existing_user(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = User.objects.create_user(phone="+919000000005")
    calls = {"n": 0}
    real_filter = User.objects.filter

    def filter_missing_once(**kwargs: object):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return User.objects.none() if calls["n"] == 1 else real_filter(**kwargs)

    monkeypatch.setattr(User.objects, "filter", filter_missing_once)
    assert services._get_or_create_user(OtpChannel.SMS, "+919000000005") == existing
