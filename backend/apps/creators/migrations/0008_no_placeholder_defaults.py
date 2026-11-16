"""No made-up data.

1. `primary_platform` and `replies_within` become NULL-able with no default, so a new creator shows "not set"
   instead of an Instagram / "replies within 24h" claim they never made. Existing rows keep their values
   (there's no way to tell a chosen 24h from the old default). Production also carries database-level
   defaults from the unmerged dev branch's migrations; those are dropped too.
2. Clears the placeholder stats that reached production: 0007 wrote 25000 followers onto Demo Creator and
   12000 onto Thalla Aravind, and Thalla's Instagram also holds the frontend's placeholder set
   (12000 followers / 3000 avg views / 4.2%). A creator's stats are cleared only while they still exactly
   match those placeholders, so anything the creator has since entered for real is kept. Handles are kept.
"""

from typing import Any

from django.db import migrations, models

# slug -> (instagram stats that are placeholders, placeholder follower_count)
KNOWN_DUMMY_STATS: dict[str, tuple[dict[str, Any], int]] = {
    "demo-creator": ({"followersCount": 25000, "avgViews": None, "engagementRate": None}, 25000),
    "thalla-aravind-67c664": ({"followersCount": 12000, "avgViews": 3000, "engagementRate": 4.2}, 12000),
}
_EMPTY_STATS = {"followersCount": None, "avgViews": None, "engagementRate": None}


def _same(stats: dict[str, Any], dummy: dict[str, Any]) -> bool:
    return all(stats.get(k) == v for k, v in dummy.items())


def clear_known_dummy_stats(apps: Any, _schema_editor: Any) -> None:
    creator_model = apps.get_model("creators", "Creator")
    for slug, (dummy_stats, dummy_followers) in KNOWN_DUMMY_STATS.items():
        creator = creator_model.objects.filter(slug=slug).first()
        if creator is None:
            continue
        fields = []
        stats = dict(creator.platform_stats or {})
        instagram = stats.get("instagram")
        if isinstance(instagram, dict) and _same(instagram, dummy_stats):
            stats["instagram"] = {**instagram, **_EMPTY_STATS}
            creator.platform_stats = stats
            fields.append("platform_stats")
        if creator.follower_count == dummy_followers and "platform_stats" in fields:
            creator.follower_count = None
            fields.append("follower_count")
        if fields:
            creator.save(update_fields=fields)


def drop_database_defaults(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        cur.execute('ALTER TABLE "creators_creator" DROP CONSTRAINT IF EXISTS "creator_platform_valid"')
        cur.execute('ALTER TABLE "creators_creator" ALTER COLUMN "replies_within" DROP DEFAULT')
        cur.execute('ALTER TABLE "creators_creator" ALTER COLUMN "primary_platform" DROP DEFAULT')


class Migration(migrations.Migration):
    dependencies = [
        ("creators", "0007_normalize_demo_creator_handles"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # IF EXISTS: the constraint's presence differs between fresh databases and production.
            database_operations=[migrations.RunPython(drop_database_defaults, migrations.RunPython.noop)],
            state_operations=[migrations.RemoveConstraint(model_name="creator", name="creator_platform_valid")],
        ),
        migrations.AlterField(
            model_name="creator",
            name="primary_platform",
            field=models.CharField(
                blank=True,
                choices=[("instagram", "Instagram"), ("youtube", "YouTube"), ("other", "Other")],
                max_length=16,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="creator",
            name="replies_within",
            field=models.IntegerField(blank=True, choices=[(1, "1h"), (6, "6h"), (24, "24h"), (72, "3d")], null=True),
        ),
        migrations.AddConstraint(
            model_name="creator",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("primary_platform__isnull", True),
                    ("primary_platform__in", ["instagram", "youtube", "other"]),
                    _connector="OR",
                ),
                name="creator_platform_valid",
            ),
        ),
        migrations.RunPython(clear_known_dummy_stats, migrations.RunPython.noop),
    ]
