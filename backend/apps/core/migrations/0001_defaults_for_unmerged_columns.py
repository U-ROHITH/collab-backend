"""Give database defaults to NOT NULL columns that exist in production but not in these models.

On 2026-11-11 the unmerged `dev` branch's migrations were applied to the production database. They added
NOT NULL columns with no database default (Django sets defaults in Python, not in the schema), so every
INSERT from this code failed: new sign-ups returned 500. Setting a database default makes those inserts
valid without dropping anything the other branch may still rely on.

Idempotent and a no-op where the columns don't exist (fresh databases, tests). Not reversed on rollback:
removing the defaults would only bring the outage back.
"""

from typing import Any

from django.db import migrations

# (table, column, SQL default). Values match the other branch's model defaults.
UNMERGED_COLUMN_DEFAULTS = (
    ("identity_user", "email_verified", "false"),
    ("creators_creator", "social_media_links", "'{}'::jsonb"),
    ("creators_creator", "social_accounts", "'[]'::jsonb"),
)


def set_defaults(_apps: Any, schema_editor: Any) -> None:
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        for table, column, default in UNMERGED_COLUMN_DEFAULTS:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
                  AND column_default IS NULL
                """,
                [table, column],
            )
            if cur.fetchone():
                cur.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET DEFAULT {default}')  # noqa: S608


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0001_initial"),
        ("creators", "0003_creatormedia"),
    ]

    operations = [migrations.RunPython(set_defaults, migrations.RunPython.noop)]
