from django.db import migrations
from django.utils.text import slugify


def populate_categories(apps, schema_editor):
    Creator = apps.get_model("creators", "Creator")
    Category = apps.get_model("creators", "Category")

    seen_categories = set()
    for creator in Creator.objects.all():
        cats = creator.categories or []
        for cat in cats:
            name = cat.strip()
            if name and name.lower() not in seen_categories:
                seen_categories.add(name.lower())
                slug = slugify(name) or name.lower()
                base_slug = slug
                counter = 1
                while Category.objects.filter(slug=slug).exists():
                    slug = f"{base_slug}-{counter}"
                    counter += 1
                Category.objects.get_or_create(
                    name=name,
                    defaults={"slug": slug, "description": "", "icon": "", "is_active": True},
                )
        if not creator.tags and cats:
            creator.tags = cats[:4]
            creator.save(update_fields=["tags"])


def unpopulate_categories(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("creators", "0009_category_creator_badges_creator_featured_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_categories, reverse_code=unpopulate_categories),
    ]

