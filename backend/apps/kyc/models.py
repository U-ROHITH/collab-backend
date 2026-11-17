from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel


class KycStatus(models.TextChoices):
    NOT_STARTED = "not_started"  # no step saved yet
    IN_PROGRESS = "in_progress"  # some steps saved, not submitted
    PENDING = "pending"  # submitted, waiting for ops review (locked)
    ACTION_NEEDED = "action_needed"  # ops requested more info; user updates and resubmits
    VERIFIED = "verified"  # can transact (locked)
    REJECTED = "rejected"  # ops rejected; user fixes steps and resubmits


class IdType(models.TextChoices):
    AADHAAR = "aadhaar"
    PASSPORT = "passport"
    DRIVING_LICENCE = "driving_licence"
    VOTER_ID = "voter_id"


class KycProfile(BaseModel):
    """One per user (brand owner or creator).

    Full PAN and bank account number are stored only as AES-GCM ciphertext (apps.core.crypto);
    the API and admin show the last 4 digits. The ID document lives in the private bucket.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="kyc")
    status = models.CharField(max_length=16, choices=KycStatus.choices, default=KycStatus.NOT_STARTED)

    pan_name = models.CharField(max_length=120, blank=True)
    pan_last4 = models.CharField(max_length=4, blank=True)
    pan_ciphertext = models.TextField(blank=True)

    id_type = models.CharField(max_length=20, choices=IdType.choices, blank=True)
    id_document = models.ForeignKey(
        "uploads.Upload", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    bank_holder_name = models.CharField(max_length=120, blank=True)
    bank_account_last4 = models.CharField(max_length=4, blank=True)
    bank_account_ciphertext = models.TextField(blank=True)
    bank_ifsc = models.CharField(max_length=11, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rejection_reason = models.CharField(max_length=300, blank=True)
    review_message = models.TextField(blank=True)
    requested_items = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(status__in=KycStatus.values), name="kyc_status_valid")]
        indexes = [models.Index(fields=["status", "submitted_at"])]

    @property
    def pan_done(self) -> bool:
        return bool(self.pan_ciphertext)

    @property
    def id_done(self) -> bool:
        return bool(self.id_type and self.id_document_id)

    @property
    def bank_done(self) -> bool:
        return bool(self.bank_account_ciphertext)

    def __str__(self) -> str:
        return f"KYC {self.user} ({self.status})"


class VerificationKind(models.TextChoices):
    CREATOR = "creator"
    BRAND = "brand"


class VerificationStatus(models.TextChoices):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INFO_REQUESTED = "info_requested"


class VerificationRequest(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="verification_requests")
    kyc_profile = models.ForeignKey(KycProfile, on_delete=models.CASCADE, related_name="verification_requests")
    kind = models.CharField(max_length=16, choices=VerificationKind.choices, db_index=True)
    status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING, db_index=True
    )
    sla_due_at = models.DateTimeField(db_index=True)
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_verifications"
    )
    decision_reason = models.CharField(max_length=500, blank=True)
    decision_note = models.TextField(blank=True)
    requested_items = models.JSONField(default=list, blank=True)
    checks = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("sla_due_at",)
        indexes = [
            models.Index(fields=["status", "sla_due_at"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self) -> str:
        return f"Verification ({self.kind}) {self.user} - {self.status}"
