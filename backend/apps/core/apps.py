from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from apps.core.db import enable_rls_on_all_tables

        post_migrate.connect(enable_rls_on_all_tables, sender=self)
