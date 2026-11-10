from typing import Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.campaigns.models import Campaign
from apps.core.cache import invalidate_campaigns_cache


@receiver(post_save, sender=Campaign)
@receiver(post_delete, sender=Campaign)
def on_campaign_change(sender: type[Campaign], instance: Campaign, **kwargs: Any) -> None:
    invalidate_campaigns_cache()
