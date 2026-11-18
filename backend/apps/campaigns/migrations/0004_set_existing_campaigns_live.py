from django.db import migrations


def set_existing_campaigns_live(apps, schema_editor):
    Campaign = apps.get_model("campaigns", "Campaign")
    Campaign.objects.all().update(
        moderation_status="live",
        posted_at=models_f("created_at"),
        submitted_at=models_f("created_at"),
    )


def models_f(field):
    from django.db.models import F
    return F(field)


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0003_remove_bid_bid_status_valid_campaign_changed_fields_and_more"),
    ]

    operations = [
        migrations.RunPython(set_existing_campaigns_live, migrations.RunPython.noop),
    ]

