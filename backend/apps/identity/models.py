from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel


class Role(models.TextChoices):
    BRAND = "brand", "Brand"
    CREATOR = "creator", "Creator"
    ADMIN = "admin", "Admin"


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    DEACTIVATED = "deactivated", "Deactivated"


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(
        self, *, email: str | None = None, phone: str | None = None, password: str | None = None, **extra: object
    ) -> "User":
        if not email and not phone:
            raise ValueError("Email or phone is required.")
        user = self.model(email=self.normalize_email(email) if email else None, phone=phone or None, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # app users log in with OTP only
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra: object) -> "User":
        extra.update(is_staff=True, is_superuser=True, role=Role.ADMIN)
        return self.create_user(email=email, password=password, **extra)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=16, unique=True, null=True, blank=True)  # noqa: DJ001 - NULL keeps unique() usable
    full_name = models.CharField(max_length=120, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, null=True, blank=True)  # noqa: DJ001 - NULL = not onboarded
    status = models.CharField(max_length=16, choices=UserStatus.choices, default=UserStatus.ACTIVE)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(email__isnull=False) | Q(phone__isnull=False), name="user_has_email_or_phone"
            ),
            models.CheckConstraint(condition=Q(role__isnull=True) | Q(role__in=Role.values), name="user_role_valid"),
            models.CheckConstraint(condition=Q(status__in=UserStatus.values), name="user_status_valid"),
        ]

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        # Checked on every authenticated request: suspended/deactivated users are locked out immediately.
        return self.status == UserStatus.ACTIVE

    def __str__(self) -> str:
        return self.email or self.phone or str(self.id)


class OtpChannel(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class OtpChallenge(BaseModel):
    """One-time login code. Only an HMAC of the code is stored."""

    channel = models.CharField(max_length=8, choices=OtpChannel.choices)
    destination = models.CharField(max_length=254, db_index=True)  # normalized phone (E.164) or email
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["destination", "created_at"])]


class AuditLog(BaseModel):
    """Append-only record of sensitive actions (role grants, admin actions, money)."""

    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["entity_type", "entity_id"])]
