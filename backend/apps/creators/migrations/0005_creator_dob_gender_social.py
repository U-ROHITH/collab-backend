"""Add the Creator columns from 00e2475 (dob, gender, social_media_links, social_accounts) that shipped without a
migration, so they work on both fresh databases and production.

Production already has these columns: the unmerged `dev` branch's migrations created them on 2026-11-11, with a
different shape. There `gender` is a nullable varchar(20) under the check constraint `creator_gender_valid`
(NULL or male/female/non_binary/other/prefer_not_to_say). This code writes '' for "not given" and accepts free
text up to 40 characters, so every creator insert broke that constraint and sign-up returned 500.

The database step is idempotent: it adds a column only where it's missing, widens `gender` to the model's shape
(NOT NULL, default '', 40 characters, '' in place of NULL) and drops the dev-only constraint. No data is lost.
Not reversed on rollback: putting the constraint back would only bring the outage back.
"""

from typing import Any

from django.db import migrations, models

_SQL = (
    'ALTER TABLE "creators_creator" ADD COLUMN IF NOT EXISTS "dob" date NULL',
    'ALTER TABLE "creators_creator" ADD COLUMN IF NOT EXISTS "gender" varchar(40) NOT NULL DEFAULT \'\'',
    'ALTER TABLE "creators_creator" ADD COLUMN IF NOT EXISTS "social_media_links" jsonb NOT NULL DEFAULT \'{}\'::jsonb',
    'ALTER TABLE "creators_creator" ADD COLUMN IF NOT EXISTS "social_accounts" jsonb NOT NULL DEFAULT \'[]\'::jsonb',
    'ALTER TABLE "creators_creator" DROP CONSTRAINT IF EXISTS "creator_gender_valid"',
    'ALTER TABLE "creators_creator" ALTER COLUMN "gender" TYPE varchar(40)',
    'UPDATE "creators_creator" SET "gender" = \'\' WHERE "gender" IS NULL',
    'ALTER TABLE "creators_creator" ALTER COLUMN "gender" SET DEFAULT \'\'',
    'ALTER TABLE "creators_creator" ALTER COLUMN "gender" SET NOT NULL',
)


def forwards(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for statement in _SQL:
            cur.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("creators", "0004_replies_within_hours"),
        ("core", "0001_defaults_for_unmerged_columns"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(forwards, migrations.RunPython.noop)],
            state_operations=[
                migrations.AddField(model_name="creator", name="dob", field=models.DateField(blank=True, null=True)),
                migrations.AddField(
                    model_name="creator", name="gender", field=models.CharField(blank=True, max_length=40)
                ),
                migrations.AddField(
                    model_name="creator",
                    name="social_media_links",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="creator",
                    name="social_accounts",
                    field=models.JSONField(blank=True, default=list),
                ),
            ],
        ),
    ]
