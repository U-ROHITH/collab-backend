from django.apps import AppConfig


class CampaignsConfig(AppConfig):
    name = "apps.campaigns"
    label = "campaigns"

    def ready(self) -> None:
        import apps.campaigns.signals  # noqa: F401
