from typing import Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.core.cache import invalidate_creator_cache
from apps.creators.models import Creator, CreatorPackage
from apps.creators.services import sync_creator_starting_price


@receiver(post_save, sender=CreatorPackage)
@receiver(post_delete, sender=CreatorPackage)
def on_creator_package_change(sender: type[CreatorPackage], instance: CreatorPackage, **kwargs: Any) -> None:
    sync_creator_starting_price(instance.creator_id)
    invalidate_creator_cache(instance.creator_id)


@receiver(post_save, sender=Creator)
@receiver(post_delete, sender=Creator)
def on_creator_change(sender: type[Creator], instance: Creator, **kwargs: Any) -> None:
    invalidate_creator_cache(instance.pk)
