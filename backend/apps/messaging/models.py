from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Conversation(BaseModel):
    """One thread per brand-creator pair."""

    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="conversations")
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="conversations")
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    brand_last_read_at = models.DateTimeField(null=True, blank=True)
    creator_last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-last_message_at", "-created_at")
        constraints = [models.UniqueConstraint(fields=["brand", "creator"], name="one_conversation_per_pair")]


class Message(BaseModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=4000, blank=True)
    attachment = models.OneToOneField(
        "uploads.Upload", null=True, blank=True, on_delete=models.PROTECT, related_name="message"
    )

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(body="") | Q(attachment__isnull=False), name="message_has_body_or_attachment"
            )
        ]
        indexes = [models.Index(fields=["conversation", "-created_at"])]
