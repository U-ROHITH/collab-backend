from django.apps import AppConfig


class CreatorsConfig(AppConfig):
    name = "apps.creators"
    label = "creators"

    def ready(self) -> None:
        import apps.creators.signals  # noqa: F401
