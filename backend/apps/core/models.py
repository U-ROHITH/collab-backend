from django.db import models
from uuid6 import uuid7


class BaseModel(models.Model):
    """UUIDv7 primary key (non-guessable, time-sortable) + timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("-created_at",)
