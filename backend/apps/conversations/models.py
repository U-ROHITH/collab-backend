from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Conversation(BaseModel):
    brand = models.ForeignKey("brands.Brand", on_delete=models.CASCADE, related_name="conversations")
    creator = models.ForeignKey("creators.Creator", on_delete=models.CASCADE, related_name="conversations")
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-last_message_at", "-updated_at")
        constraints = [
            models.UniqueConstraint(fields=["brand", "creator"], name="uniq_conversation_brand_creator"),
        ]

    def __str__(self) -> str:
        return f"Conversation(brand={self.brand_id}, creator={self.creator_id})"


class Message(BaseModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    sender_role = models.CharField(max_length=16)
    content = models.TextField(max_length=5000)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["conversation", "is_read"]),
        ]

    def __str__(self) -> str:
        return f"Message({self.id}, conv={self.conversation_id}, sender={self.sender_id})"
