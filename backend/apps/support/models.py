from django.conf import settings
from django.db import models
from django.db.models import Max, Q

from apps.core.models import BaseModel


class TicketTopic(models.TextChoices):
    PAYMENTS = "payments", "Payments"
    DEAL = "deal", "Deal"
    ACCOUNT = "account", "Account"
    MARKETPLACE = "marketplace", "Marketplace"
    BUG = "bug", "Bug"
    OTHER = "other", "Other"


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    WAITING_ON_USER = "waiting_on_user", "Waiting on User"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class MessageAuthorType(models.TextChoices):
    USER = "user", "User"
    ADMIN = "admin", "Admin"
    SYSTEM = "system", "System"


def next_ticket_number() -> int:
    max_num = SupportTicket.objects.aggregate(m=Max("number"))["m"]
    return (max_num or 1041) + 1


class SupportTicket(BaseModel):
    number = models.BigIntegerField(unique=True, db_index=True, default=next_ticket_number)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_tickets")
    role = models.CharField(max_length=16, blank=True)
    topic = models.CharField(max_length=32, choices=TicketTopic.choices)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    subject = models.CharField(max_length=120)
    status = models.CharField(max_length=24, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    priority = models.CharField(max_length=16, choices=TicketPriority.choices, default=TicketPriority.NORMAL)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_tickets",
    )
    last_user_message_at = models.DateTimeField(null=True, blank=True)
    last_admin_message_at = models.DateTimeField(null=True, blank=True)
    unread_for_user = models.PositiveIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    @property
    def formatted_number(self) -> str:
        return f"LL-{self.number}"

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(status__in=TicketStatus.values), name="support_ticket_status_valid"),
            models.CheckConstraint(
                condition=Q(priority__in=TicketPriority.values), name="support_ticket_priority_valid"
            ),
            models.CheckConstraint(condition=Q(topic__in=TicketTopic.values), name="support_ticket_topic_valid"),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-last_user_message_at"]),
        ]


class SupportMessage(BaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author_type = models.CharField(max_length=16, choices=MessageAuthorType.choices)
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_messages",
    )
    internal = models.BooleanField(default=False)
    text = models.TextField()
    attachment_ids = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
        ]
