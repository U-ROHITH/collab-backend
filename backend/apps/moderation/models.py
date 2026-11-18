from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class AbuseTargetType(models.TextChoices):
    CREATOR = "creator", "Creator"
    BRAND = "brand", "Brand"
    CAMPAIGN = "campaign", "Campaign"
    REVIEW = "review", "Review"
    MESSAGE = "message", "Message"


class AbuseReportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    INVESTIGATING = "investigating", "Investigating"
    ACTIONED = "actioned", "Actioned"
    DISMISSED = "dismissed", "Dismissed"


class AbuseReport(BaseModel):
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="abuse_reports")
    target_type = models.CharField(max_length=32, choices=AbuseTargetType.choices)
    target_id = models.UUIDField()
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_received",
    )
    reason = models.CharField(max_length=100)
    details = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=AbuseReportStatus.choices, default=AbuseReportStatus.PENDING, db_index=True
    )
    action_taken = models.CharField(max_length=100, blank=True, default="")
    action_notes = models.TextField(blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "target_type", "target_id"],
                name="one_report_per_target_per_user",
            ),
            models.CheckConstraint(
                condition=Q(status__in=AbuseReportStatus.values),
                name="abuse_report_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(target_type__in=AbuseTargetType.values),
                name="abuse_report_target_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["status", "created_at"]),
        ]


class BidAlertType(models.TextChoices):
    BID_SPIKE = "bid_spike", "Bid Spike"
    COLLUSION_PATTERN = "collusion_pattern", "Collusion Pattern"
    SUSPICIOUS_LOW_BID = "suspicious_low_bid", "Suspicious Low Bid"
    UNVERIFIED_BIDDER_CLUSTER = "unverified_bidder_cluster", "Unverified Bidder Cluster"


class BidAlertSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class BidAlertStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FROZEN = "frozen", "Frozen"
    DISMISSED = "dismissed", "Dismissed"
    ACTIONED = "actioned", "Actioned"


class BidAlert(BaseModel):
    campaign = models.ForeignKey("campaigns.Campaign", on_delete=models.CASCADE, related_name="bid_alerts")
    alert_type = models.CharField(max_length=64, choices=BidAlertType.choices)
    severity = models.CharField(
        max_length=16, choices=BidAlertSeverity.choices, default=BidAlertSeverity.MEDIUM
    )
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=BidAlertStatus.choices, default=BidAlertStatus.PENDING, db_index=True
    )
    bids = models.ManyToManyField("campaigns.Bid", blank=True, related_name="alerts")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=BidAlertStatus.values),
                name="bid_alert_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(severity__in=BidAlertSeverity.values),
                name="bid_alert_severity_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "severity", "created_at"]),
        ]

