from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from apps.core.db import enable_rls_on_all_tables

        post_migrate.connect(enable_rls_on_all_tables, sender=self)

        from django.db.models.signals import post_delete, post_save

        from apps.core.public_cache import WATCHED_MODELS, invalidate

        for model in WATCHED_MODELS:
            post_save.connect(invalidate, sender=model, dispatch_uid=f"public-cache-save-{model}")
            post_delete.connect(invalidate, sender=model, dispatch_uid=f"public-cache-delete-{model}")
