"""Store `replies_within` as integer hours.

Production already has an integer column: the unmerged `dev` branch's migrations converted it on
2026-11-11, which broke creator sign-up for this code (it wrote "24h" into an integer column). This brings
the model in line. The database step only converts where the column is still text (fresh and local
databases); where it's already an integer it does nothing. The API still shows "1h" | "6h" | "24h" | "3d".
"""

from typing import Any

from django.db import migrations, models

_TO_HOURS = "CASE replies_within WHEN '1h' THEN 1 WHEN '6h' THEN 6 WHEN '3d' THEN 72 ELSE 24 END"
_TO_LABEL = "CASE replies_within WHEN 1 THEN '1h' WHEN 6 THEN '6h' WHEN 72 THEN '3d' ELSE '24h' END"


def _column_type(schema_editor: Any) -> str | None:
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'creators_creator' AND column_name = 'replies_within'"
        )
        row = cur.fetchone()
    return row[0] if row else None


def to_hours(_apps: Any, schema_editor: Any) -> None:
    if _column_type(schema_editor) == "character varying":
        with schema_editor.connection.cursor() as cur:
            cur.execute(
                f'ALTER TABLE "creators_creator" ALTER COLUMN "replies_within" TYPE integer USING ({_TO_HOURS})'
            )


def to_label(_apps: Any, schema_editor: Any) -> None:
    if _column_type(schema_editor) == "integer":
        with schema_editor.connection.cursor() as cur:
            cur.execute(
                f'ALTER TABLE "creators_creator" ALTER COLUMN "replies_within" TYPE varchar(4) USING ({_TO_LABEL})'
            )


class Migration(migrations.Migration):
    dependencies = [("creators", "0003_creatormedia")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(to_hours, to_label)],
            state_operations=[
                migrations.AlterField(
                    model_name="creator",
                    name="replies_within",
                    field=models.IntegerField(choices=[(1, "1h"), (6, "6h"), (24, "24h"), (72, "3d")], default=24),
                ),
            ],
        ),
    ]
