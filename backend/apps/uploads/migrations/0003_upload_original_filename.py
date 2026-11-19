from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("uploads", "0002_remove_upload_upload_kind_valid_alter_upload_kind_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="upload",
            name="original_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
