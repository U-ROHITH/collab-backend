from django.db import migrations


def normalize_demo_creator(apps, schema_editor):
    Creator = apps.get_model("creators", "Creator")
    demo = Creator.objects.filter(slug="demo-creator").first()
    if demo:
        links = dict(demo.social_media_links or {})
        links["instagram"] = "democreator"
        demo.social_media_links = links
        stats = dict(demo.platform_stats or {})
        stats["instagram"] = {
            "followersCount": 25000,
            "avgViews": None,
            "engagementRate": None,
        }
        demo.platform_stats = stats
        demo.follower_count = 25000
        demo.save(update_fields=["social_media_links", "platform_stats", "follower_count"])

    aravind = Creator.objects.filter(slug="thalla-aravind-67c664").first()
    if aravind:
        aravind.follower_count = 12000
        aravind.save(update_fields=["follower_count"])


def reverse_func(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("creators", "0006_creator_platform_stats"),
    ]

    operations = [
        migrations.RunPython(normalize_demo_creator, reverse_func),
    ]
